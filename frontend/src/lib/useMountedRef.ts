import { useEffect, useRef } from 'react';

/** A ref that is true while the component is mounted, for guarding async state updates. */
export function useMountedRef() {
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  return mounted;
}
