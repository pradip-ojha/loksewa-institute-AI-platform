import { useEffect, useRef } from "react";

/**
 * Returns a ref for an end-of-conversation sentinel div; scrolls it into view
 * whenever `dep` changes (typically the turns array).
 */
export function useAutoScrollEnd(dep: unknown, block: ScrollLogicalPosition = "end") {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block });
  }, [dep, block]);
  return endRef;
}
