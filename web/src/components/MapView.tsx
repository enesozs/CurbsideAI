import { useEffect, useRef, useState } from "react";
import maplibregl, { type ExpressionSpecification, type MapLayerMouseEvent } from "maplibre-gl";
import { Protocol } from "pmtiles";
import type { Meta } from "../lib/data";
import { basemapStyle } from "../lib/basemap";
import { fillColorExpr, type Measure, type Theme } from "../lib/scales";
import { MISSING } from "../lib/values";

const protocol = new Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const FIRST_LABEL = "address_label";
const NYC_BOUNDS: [number, number, number, number] = [-74.3, 40.47, -73.65, 40.95];

function hatchImage(theme: Theme): ImageData {
  const size = 8;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d")!;
  ctx.fillStyle = theme === "dark" ? "rgba(40,40,38,0.85)" : "rgba(236,235,230,0.9)";
  ctx.fillRect(0, 0, size, size);
  ctx.strokeStyle = theme === "dark" ? "#6f6d68" : "#a9a79f";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(0, size);
  ctx.lineTo(size, 0);
  ctx.stroke();
  return ctx.getImageData(0, 0, size, size);
}

interface Props {
  meta: Meta;
  values: Float64Array | null;
  measure: Measure;
  theme: Theme;
  borough: string;
  selected: number | null;
  onSelect: (id: number | null) => void;
  onHover: (id: number | null, point: { x: number; y: number } | null) => void;
}

export function MapView({ meta, values, measure, theme, borough, selected, onSelect, onHover }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const hoverRef = useRef<number | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const handlers = useRef({ onSelect, onHover });
  useEffect(() => {
    handlers.current = { onSelect, onHover };
  });

  // Create / recreate the map when the theme changes (basemap flavor differs per theme).
  useEffect(() => {
    if (!ref.current) return;
    setReady(false);
    const map = new maplibregl.Map({
      container: ref.current,
      style: basemapStyle(theme),
      bounds: [-74.06, 40.56, -73.77, 40.89],
      maxBounds: [
        [NYC_BOUNDS[0] - 0.15, NYC_BOUNDS[1] - 0.1],
        [NYC_BOUNDS[2] + 0.15, NYC_BOUNDS[3] + 0.1],
      ],
      minZoom: 9,
      maxZoom: 16,
      attributionControl: { compact: true },
      dragRotate: false,
      pitchWithRotate: false,
    });
    map.touchZoomRotate.disableRotation();
    map.keyboard.disableRotation();
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    mapRef.current = map;
    if (import.meta.env.DEV) (window as unknown as { __map: maplibregl.Map }).__map = map;

    map.on("error", (e) => {
      const msg = (e.error && e.error.message) || "";
      if (/pbf|glyph|sprite/i.test(msg)) return; // missing optional glyph ranges are harmless
      setError(msg || "Map failed to load");
    });

    map.on("load", () => {
      map.addImage("hatch", hatchImage(theme), { pixelRatio: 2 });
      map.addSource("zones", { type: "geojson", data: "/data/zones.geojson", promoteId: "id" });
      const labels = map.getLayer(FIRST_LABEL) ? FIRST_LABEL : undefined;
      // Fills sit above roads (semi-transparent, so the street grid reads through) and below labels.
      const before = labels;
      map.addLayer(
        {
          id: "zones-fill",
          type: "fill",
          source: "zones",
          paint: {
            "fill-color": fillColorExpr("count", theme) as ExpressionSpecification,
            "fill-opacity": ["case", ["<", ["coalesce", ["feature-state", "v"], -1], 0], 0, theme === "dark" ? 0.82 : 0.78],
          },
        },
        before,
      );
      map.addLayer(
        {
          id: "zones-missing",
          type: "fill",
          source: "zones",
          paint: {
            "fill-pattern": "hatch",
            "fill-opacity": ["case", ["<", ["coalesce", ["feature-state", "v"], -1], 0], 1, 0],
          },
        },
        before,
      );
      map.addLayer(
        {
          id: "zones-dim",
          type: "fill",
          source: "zones",
          filter: ["==", ["get", "borough"], "__none__"],
          paint: { "fill-color": theme === "dark" ? "#0d0d0d" : "#f9f9f7", "fill-opacity": 0.72 },
        },
        labels,
      );
      map.addLayer(
        {
          id: "zones-line",
          type: "line",
          source: "zones",
          paint: {
            "line-color": theme === "dark" ? "rgba(255,255,255,0.22)" : "rgba(11,11,11,0.22)",
            "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.3, 13, 1],
          },
        },
        labels,
      );
      map.addLayer(
        {
          id: "zones-hover",
          type: "line",
          source: "zones",
          paint: {
            "line-color": theme === "dark" ? "#f0efec" : "#0b0b0b",
            "line-width": 1.6,
            "line-opacity": ["case", ["boolean", ["feature-state", "hover"], false], 1, 0],
          },
        },
        labels,
      );
      map.addLayer(
        {
          id: "zones-selected",
          type: "line",
          source: "zones",
          filter: ["==", ["get", "id"], -1],
          paint: { "line-color": theme === "dark" ? "#f0efec" : "#0b0b0b", "line-width": 3 },
        },
        labels,
      );

      const setHover = (id: number | null) => {
        if (hoverRef.current !== null) map.setFeatureState({ source: "zones", id: hoverRef.current }, { hover: false });
        hoverRef.current = id;
        if (id !== null) map.setFeatureState({ source: "zones", id }, { hover: true });
      };
      map.on("mousemove", "zones-fill", (e: MapLayerMouseEvent) => {
        const id = Number(e.features?.[0]?.id);
        map.getCanvas().style.cursor = "pointer";
        if (id !== hoverRef.current) setHover(id);
        handlers.current.onHover(id, { x: e.point.x, y: e.point.y });
      });
      map.on("mouseleave", "zones-fill", () => {
        map.getCanvas().style.cursor = "";
        setHover(null);
        handlers.current.onHover(null, null);
      });
      map.on("click", (e) => {
        const f = map.queryRenderedFeatures(e.point, { layers: ["zones-fill", "zones-missing"] })[0];
        handlers.current.onSelect(f ? Number(f.id) : null);
      });
      map.once("sourcedata", () => setReady(true));
      map.on("idle", () => setReady(true));
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [theme]);

  // Colour scale follows the measure.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !map.getLayer("zones-fill")) return;
    map.setPaintProperty("zones-fill", "fill-color", fillColorExpr(measure, theme) as ExpressionSpecification);
  }, [measure, theme, ready]);

  // Push the current hour's values into feature-state (fast: no GeoJSON re-parse).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !map.getSource("zones")) return;
    meta.zones.forEach((z, zi) => {
      const v = values ? values[zi] : MISSING;
      map.setFeatureState({ source: "zones", id: z.id }, { v });
    });
  }, [values, ready, meta]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !map.getLayer("zones-dim")) return;
    map.setFilter("zones-dim", borough === "All" ? ["==", ["get", "borough"], "__none__"] : ["!=", ["get", "borough"], borough]);
  }, [borough, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !map.getLayer("zones-selected")) return;
    map.setFilter("zones-selected", ["==", ["get", "id"], selected ?? -1]);
  }, [selected, ready]);

  return (
    <div className="absolute inset-0" aria-label="Map of New York City taxi zones" role="region">
      {/* MapLibre sets position:relative on its container, so size it explicitly. */}
      <div ref={ref} className="h-full w-full" />
      {!ready && !error && (
        <div className="absolute inset-0 grid place-items-center pointer-events-none">
          <div className="panel px-4 py-2 text-sm text-[var(--ink-2)]">Loading map…</div>
        </div>
      )}
      {error && (
        <div className="absolute inset-0 grid place-items-center">
          <div className="panel px-5 py-4 text-sm max-w-sm">
            <div className="font-semibold mb-1">The map could not load</div>
            <div className="text-[var(--ink-2)]">{error}</div>
          </div>
        </div>
      )}
    </div>
  );
}
