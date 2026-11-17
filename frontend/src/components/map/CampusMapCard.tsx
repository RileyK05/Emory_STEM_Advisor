import { useMemo, useState } from 'react';
import { useLoad } from '../../lib/useLoad';
import { CampusMap } from './CampusMap';
import { directionsUrl, EMORY_MAP_URL, loadCampusMap, placesIn, type CampusMapData } from './mapData';

/** The map card under an answer that names campus places. The text list below
 *  the map says the same thing as the pins, so the card works without sight. */
export function CampusMapCard({ placeIds, campus = 'atlanta' }: { placeIds: readonly string[]; campus?: string }) {
  const [map, setMap] = useState<CampusMapData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  useLoad(() => loadCampusMap(campus), [campus], { onLoad: setMap, setError });

  const places = useMemo(() => (map ? placesIn(map, placeIds) : []), [map, placeIds]);
  if (error) return null;
  if (!map) return <div className="campus-map-card campus-map-loading" aria-busy="true" />;
  if (!places.length) return null;

  const names = places.map((place) => place.name).join(', ');
  return (
    <section className="campus-map-card" aria-label={`Campus map: ${names}`}>
      <CampusMap
        map={map}
        highlight={places}
        aspect={expanded ? 4 / 3 : 16 / 9}
        label={`Map of ${map.name} showing ${names}`}
      />
      <ul className="campus-map-places">
        {places.map((place) => {
          const directions = directionsUrl(place);
          return (
            <li key={place.id}>
              <strong>{place.name}</strong>
              {directions && (
                <a href={directions} target="_blank" rel="noopener noreferrer">
                  Directions
                </a>
              )}
              <a href={EMORY_MAP_URL} target="_blank" rel="noopener noreferrer">
                Emory’s map
              </a>
            </li>
          );
        })}
      </ul>
      <button type="button" className="link-button" onClick={() => setExpanded((value) => !value)}>
        {expanded ? 'Smaller map' : 'Bigger map'}
      </button>
    </section>
  );
}
