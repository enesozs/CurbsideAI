import type maplibregl from "maplibre-gl";
import { layers, namedFlavor } from "@protomaps/basemaps";
import type { Theme } from "./scales";

/** Self-hosted Protomaps style: grayscale (light) or black (dark) flavor, local glyphs and sprites. */
export function basemapStyle(theme: Theme): maplibregl.StyleSpecification {
  const origin = window.location.origin;
  const flavor = theme === "dark" ? "black" : "grayscale";
  return {
    version: 8,
    glyphs: `${origin}/basemap/fonts/{fontstack}/{range}.pbf`,
    sprite: `${origin}/basemap/sprites/v4/${flavor}`,
    sources: {
      protomaps: {
        type: "vector",
        url: `pmtiles://${origin}/basemap/nyc.pmtiles`,
        attribution:
          '<a href="https://protomaps.com">Protomaps</a> © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors (ODbL) · Trips: <a href="https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page">NYC TLC</a>',
      },
    },
    layers: layers("protomaps", namedFlavor(flavor), { lang: "en" }) as maplibregl.LayerSpecification[],
  };
}
