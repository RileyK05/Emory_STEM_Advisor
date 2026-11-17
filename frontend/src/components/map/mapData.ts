/** Our own campus map format, written by `scripts.build_campus_map`.
 *  Coordinates are metres from the campus origin, x east and y south, stored flat
 *  ([x0, y0, x1, y1, ...]) so they map straight onto an SVG viewBox. */

export type AreaKind = 'lawn' | 'woods' | 'sport' | 'water' | 'parking' | 'plaza';
export type LineKind = 'road-major' | 'road' | 'path' | 'stream';

export interface MapBuilding {
  /** Present only for a named building inside the campus boundary. */
  id?: string;
  name?: string;
  osm?: string;
  /** Latitude and longitude of the label point, for the Directions link. */
  at?: [number, number];
  rings: number[][];
  label: [number, number];
  area: number;
  /** Outside the campus boundary: drawn faded, never labeled. */
  context?: boolean;
}

export interface CampusMapData {
  campus: string;
  name: string;
  attribution: string;
  attributionUrl: string;
  license: string;
  origin: [number, number];
  bounds: [number, number, number, number];
  boundary: number[][];
  areas: { kind: AreaKind; rings: number[][] }[];
  lines: { kind: LineKind; points: number[]; name?: string }[];
  buildings: MapBuilding[];
}

export interface Box {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

const cache = new Map<string, Promise<CampusMapData>>();

/** Loads a campus map once per page; a failed load is forgotten so a retry can work. */
export function loadCampusMap(campus: string): Promise<CampusMapData> {
  let pending = cache.get(campus);
  if (!pending) {
    pending = fetch(`${import.meta.env.BASE_URL}maps/${campus}.json`).then(async (response) => {
      if (!response.ok) throw new Error(`The ${campus} campus map is not available (${response.status}).`);
      return (await response.json()) as CampusMapData;
    });
    pending.catch(() => cache.delete(campus));
    cache.set(campus, pending);
  }
  return pending;
}

function pathOf(points: number[], closed: boolean): string {
  if (points.length < 4) return '';
  let d = `M${points[0]} ${points[1]}`;
  for (let i = 2; i < points.length; i += 2) d += `L${points[i]} ${points[i + 1]}`;
  return closed ? `${d}Z` : d;
}

export const ringsPath = (rings: number[][]): string => rings.map((ring) => pathOf(ring, true)).join('');
export const linePath = (points: number[]): string => pathOf(points, false);

export function boxOf(rings: number[][]): Box {
  const box = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
  for (const ring of rings)
    for (let i = 0; i < ring.length; i += 2) {
      box.minX = Math.min(box.minX, ring[i]);
      box.maxX = Math.max(box.maxX, ring[i]);
      box.minY = Math.min(box.minY, ring[i + 1]);
      box.maxY = Math.max(box.maxY, ring[i + 1]);
    }
  return box;
}

/** The named on-campus buildings these place ids refer to, in the order given. */
export function placesIn(map: CampusMapData, placeIds: readonly string[]): MapBuilding[] {
  const byId = new Map(map.buildings.filter((b) => b.id).map((b) => [b.id as string, b]));
  return placeIds.flatMap((id) => byId.get(id) ?? []);
}

/** A link that opens the student's own maps app with walking directions to a building. */
export function directionsUrl(building: MapBuilding): string | null {
  if (!building.at) return null;
  const [lat, lon] = building.at;
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}&travelmode=walking`;
}

export const EMORY_MAP_URL = 'https://map.emory.edu/';
