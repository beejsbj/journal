import { useEffect } from "preact/hooks";

/** The tab's title: "<page> · journal". */
export function useTitle(page: string | null): void {
  useEffect(() => {
    document.title = page ? `${page} · journal` : "journal";
  }, [page]);
}
