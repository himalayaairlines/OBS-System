/**
 * Pagination for list screens.
 *
 * usePaged      server-side: asks the API for one page at a time
 *               (?page=&page_size=) and keeps the page in range when the
 *               total shrinks.
 * usePagedArray client-side: pages a list already held in memory, for small
 *               reference lists such as the crew roster.
 *
 * Both reset to page 1 whenever the filters (the path, or `resetKey`) change.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { api } from './api';

export const PAGE_SIZES = [10, 25, 50, 100];
const EMPTY = { items: [], total: 0, page: 1, pages: 1, page_size: 25, has_prev: false, has_next: false };

export function usePaged(path, { pageSize: initialSize = 25, enabled = true } = {}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(initialSize);
  const [data, setData] = useState(EMPTY);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const request = useRef(0);

  // New filters or a new page size start again from the first page.
  useEffect(() => { setPage(1); }, [path, pageSize]);

  const reload = useCallback(async () => {
    if (!enabled || !path) return;
    const ticket = ++request.current;
    setLoading(true);
    try {
      const sep = path.includes('?') ? '&' : '?';
      const result = await api(`${path}${sep}page=${page}&page_size=${pageSize}`);
      if (ticket !== request.current) return; // a newer request superseded this one
      setData({ ...EMPTY, ...result });
      setError(null);
      if (result.page && result.page !== page) setPage(result.page);
    } catch (e) {
      if (ticket === request.current) setError(e.message);
    } finally {
      if (ticket === request.current) setLoading(false);
    }
  }, [path, page, pageSize, enabled]);

  useEffect(() => { reload(); }, [reload]);

  return {
    items: data.items, total: data.total, page: data.page || page, pages: data.pages || 1,
    pageSize, setPage, setPageSize, loading, error, reload, raw: data,
  };
}

export function usePagedArray(items, { pageSize: initialSize = 25, resetKey } = {}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(initialSize);
  useEffect(() => { setPage(1); }, [resetKey, pageSize]);
  const total = items.length;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(page, pages);
  const slice = useMemo(
    () => items.slice((current - 1) * pageSize, current * pageSize),
    [items, current, pageSize],
  );
  return { items: slice, total, page: current, pages, pageSize, setPage, setPageSize };
}
