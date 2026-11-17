import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { boxOf, linePath, ringsPath, type Box, type CampusMapData, type MapBuilding } from './mapData';

interface View {
  cx: number;
  cy: number;
  /** Visible width in metres; the height follows from the aspect ratio. */
  w: number;
}

const MIN_WIDTH = 60;
const LABEL_PX = 12;
const AREA_KINDS = ['woods', 'lawn', 'sport', 'water', 'parking', 'plaza'] as const;
const LINE_KINDS = ['stream', 'path', 'road', 'road-major'] as const;
// A teardrop pin, 28 px tall, its tip at (0, 0).
const PIN = 'M0 0C-2-6-9-10-9-17a9 9 0 0 1 18 0C9-10 2-6 0 0Z';

function fit(box: Box, aspect: number, minWidth: number, pad: number): View {
  const w = Math.max((box.maxX - box.minX) * pad, (box.maxY - box.minY) * pad * aspect, minWidth);
  return { cx: (box.minX + box.maxX) / 2, cy: (box.minY + box.maxY) / 2, w };
}

/** Our own drawing of a campus, from `public/maps/<campus>.json`. Drag, the arrow
 *  keys, the buttons, or Ctrl + wheel move it; highlighted buildings get a pin. */
export function CampusMap({
  map,
  highlight = [],
  aspect = 16 / 10,
  label,
}: {
  map: CampusMapData;
  highlight?: MapBuilding[];
  aspect?: number;
  label: string;
}) {
  const campusBox = useMemo<Box>(() => {
    const [minX, minY, maxX, maxY] = map.bounds;
    return { minX, minY, maxX, maxY };
  }, [map]);
  const maxWidth = (campusBox.maxX - campusBox.minX) * 1.6;
  const home = useMemo<View>(() => {
    if (!highlight.length) return fit(campusBox, aspect, MIN_WIDTH, 1.05);
    return fit(boxOf(highlight.flatMap((b) => b.rings)), aspect, 260, 2.2);
  }, [highlight, campusBox, aspect]);
  const [view, setView] = useState<View>(home);
  useEffect(() => setView(home), [home]);

  const frame = useRef<HTMLDivElement>(null);
  const [pixelWidth, setPixelWidth] = useState(640);
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setPixelWidth(entry.contentRect.width || 640));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const clamp = useCallback(
    (next: View): View => ({ ...next, w: Math.min(Math.max(next.w, MIN_WIDTH), maxWidth) }),
    [maxWidth],
  );
  const zoom = useCallback(
    (factor: number, at?: { x: number; y: number }) =>
      setView((current) => {
        const next = clamp({ ...current, w: current.w * factor });
        if (!at) return next;
        // Keep the map point under the cursor where it is.
        const scale = next.w / current.w;
        return { ...next, cx: at.x + (current.cx - at.x) * scale, cy: at.y + (current.cy - at.y) * scale };
      }),
    [clamp],
  );

  const h = view.w / aspect;
  const metresPerPx = view.w / pixelWidth;
  const viewBox = `${view.cx - view.w / 2} ${view.cy - h / 2} ${view.w} ${h}`;

  // Ctrl + wheel zooms, so a plain wheel still scrolls the conversation.
  useEffect(() => {
    const element = frame.current;
    if (!element) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      const rect = element.getBoundingClientRect();
      setView((current) => {
        const ch = current.w / aspect;
        const at = {
          x: current.cx - current.w / 2 + ((event.clientX - rect.left) / rect.width) * current.w,
          y: current.cy - ch / 2 + ((event.clientY - rect.top) / rect.height) * ch,
        };
        const next = clamp({ ...current, w: current.w * Math.pow(1.0015, event.deltaY) });
        const scale = next.w / current.w;
        return { ...next, cx: at.x + (current.cx - at.x) * scale, cy: at.y + (current.cy - at.y) * scale };
      });
    };
    element.addEventListener('wheel', onWheel, { passive: false });
    return () => element.removeEventListener('wheel', onWheel);
  }, [aspect, clamp]);

  const drag = useRef<{ id: number; x: number; y: number } | null>(null);
  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const start = drag.current;
    if (!start || start.id !== event.pointerId) return;
    const dx = (event.clientX - start.x) * metresPerPx;
    const dy = (event.clientY - start.y) * metresPerPx;
    drag.current = { ...start, x: event.clientX, y: event.clientY };
    setView((current) => ({ ...current, cx: current.cx - dx, cy: current.cy - dy }));
  };
  const onPointerUp = () => {
    drag.current = null;
  };
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = view.w * 0.15;
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    if (moves[event.key]) {
      const [dx, dy] = moves[event.key];
      setView((current) => ({ ...current, cx: current.cx + dx, cy: current.cy + dy }));
    } else if (event.key === '+' || event.key === '=') zoom(1 / 1.5);
    else if (event.key === '-' || event.key === '_') zoom(1.5);
    else if (event.key === '0') setView(home);
    else return;
    event.preventDefault();
  };

  const layers = useMemo(() => {
    const areas = new Map<string, string>();
    for (const area of map.areas) areas.set(area.kind, (areas.get(area.kind) ?? '') + ringsPath(area.rings));
    const lines = new Map<string, string>();
    for (const line of map.lines) lines.set(line.kind, (lines.get(line.kind) ?? '') + linePath(line.points));
    let campus = '';
    let context = '';
    for (const building of map.buildings) {
      if (building.context) context += ringsPath(building.rings);
      else campus += ringsPath(building.rings);
    }
    return { areas, lines, campus, context, boundary: ringsPath(map.boundary) };
  }, [map]);

  // Name the biggest buildings first and skip any label that would overlap one
  // already placed; highlighted buildings are always named.
  const labels = useMemo(() => {
    const size = LABEL_PX * metresPerPx;
    const left = view.cx - view.w / 2;
    const top = view.cy - h / 2;
    const out: { key: string; x: number; y: number; text: string; strong: boolean }[] = [];
    const highlighted = new Set(highlight);
    // Every pin is reserved first, so no label (a highlighted one included) covers one.
    const placed: Box[] = highlight.map(({ label: [x, y] }) => ({
      minX: x - 10 * metresPerPx,
      maxX: x + 10 * metresPerPx,
      minY: y - 28 * metresPerPx,
      maxY: y,
    }));
    const clashes = (box: Box) =>
      placed.some((p) => box.minX < p.maxX && box.maxX > p.minX && box.minY < p.maxY && box.maxY > p.minY);
    const named = map.buildings
      .filter((b) => b.name && !b.context)
      .sort((a, b) => Number(highlighted.has(b)) - Number(highlighted.has(a)) || b.area - a.area);
    for (const building of named) {
      const strong = highlighted.has(building);
      const [x, y] = building.label;
      if (!strong && (x < left || x > left + view.w || y < top || y > top + h)) continue;
      if (!strong && Math.sqrt(building.area) / metresPerPx < 28) continue;
      const half = ((building.name as string).length * size * 0.56) / 2;
      const boxAt = (ly: number) => ({ minX: x - half, maxX: x + half, minY: ly - size * 0.8, maxY: ly + size * 0.3 });
      // A highlighted name sits above its pin, or below the pin's tip when that
      // spot covers another pin or name; it is drawn either way.
      let ly = strong ? y - 32 * metresPerPx : y;
      let box = boxAt(ly);
      if (strong && clashes(box) && !clashes(boxAt(y + size * 1.2))) {
        ly = y + size * 1.2;
        box = boxAt(ly);
      }
      const cut = box.minX < left || box.maxX > left + view.w || box.minY < top || box.maxY > top + h;
      if ((clashes(box) || cut) && !strong) continue;
      placed.push(box);
      out.push({ key: building.id ?? building.osm ?? `${x},${y}`, x, y: ly, text: building.name as string, strong });
    }
    return { items: out, size };
  }, [map, highlight, view, h, metresPerPx]);

  return (
    <div className="campus-map">
      <div
        ref={frame}
        className="campus-map-frame"
        style={{ aspectRatio: String(aspect) }}
        tabIndex={0}
        role="group"
        aria-label={`${label}. Arrow keys move the map, plus and minus zoom, 0 resets.`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onKeyDown={onKeyDown}
      >
        <svg viewBox={viewBox} role="img" aria-label={label} preserveAspectRatio="xMidYMid meet">
          <rect className="map-ground" x={view.cx - view.w} y={view.cy - h} width={view.w * 2} height={h * 2} />
          <path className="map-campus" d={layers.boundary} />
          {AREA_KINDS.map((kind) => (
            <path key={kind} className={`map-${kind}`} d={layers.areas.get(kind) ?? ''} />
          ))}
          {LINE_KINDS.map((kind) => (
            <path
              key={kind}
              className={`map-line map-${kind}`}
              d={layers.lines.get(kind) ?? ''}
              strokeWidth={kind === 'road-major' ? 9 : kind === 'road' ? 6 : kind === 'stream' ? 2.5 : 1.6}
            />
          ))}
          <path className="map-building-context" d={layers.context} />
          <path className="map-building" d={layers.campus} />
          {highlight.map((building) => (
            <path key={building.id} className="map-building-highlight" d={ringsPath(building.rings)} />
          ))}
          <g className="map-labels" fontSize={labels.size}>
            {labels.items.map((item) => (
              <text
                key={item.key}
                x={item.x}
                y={item.y}
                textAnchor="middle"
                className={item.strong ? 'map-label map-label-strong' : 'map-label'}
                strokeWidth={labels.size * 0.28}
              >
                {item.text}
              </text>
            ))}
          </g>
          {highlight.map((building) => (
            <path
              key={`pin-${building.id}`}
              className="map-pin"
              d={PIN}
              transform={`translate(${building.label[0]} ${building.label[1]}) scale(${metresPerPx})`}
            />
          ))}
        </svg>
        <div className="campus-map-controls">
          <button type="button" aria-label="Zoom in" onClick={() => zoom(1 / 1.5)}>
            +
          </button>
          <button type="button" aria-label="Zoom out" onClick={() => zoom(1.5)}>
            −
          </button>
          <button type="button" aria-label="Reset the map" onClick={() => setView(home)}>
            ⤢
          </button>
        </div>
      </div>
      <p className="campus-map-credit">
        Our own drawing. Map data{' '}
        <a href={map.attributionUrl} target="_blank" rel="noopener noreferrer">
          {map.attribution}
        </a>
        .
      </p>
    </div>
  );
}
