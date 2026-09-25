import { useCallback, useEffect, useRef, useState } from "react";

const BOTTOM_THRESHOLD_PX = 40;

const prefersReducedMotion = () =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export interface StickToBottom<T extends HTMLElement, C extends HTMLElement = HTMLElement> {
  /** Attach to the scrollable pane itself (fixed/max height, overflow-y:auto). */
  ref: React.RefObject<T>;
  /** Attach to the element wrapping the rendered items, *inside* `ref`. Its
   * natural (unconstrained) height is what actually changes when content
   * grows without a new item — e.g. an image finishing layout — which the
   * fixed-height scroll container's own box never does. */
  contentRef: React.RefObject<C>;
  /** True while the pane is pinned to the bottom (within the bottom threshold). */
  pinned: boolean;
  /** Items that arrived while the user had scrolled up to read. */
  pendingCount: number;
  onScroll: () => void;
  onKeyDown: (event: React.KeyboardEvent) => void;
  /** Force-scroll to bottom and re-arm pinning (e.g. right after the user sends a message). */
  scrollToBottom: (options?: { smooth?: boolean }) => void;
}

/**
 * Claude-Code-transcript-style scrolling for a live-appending list: newest
 * item at the bottom, pane opens scrolled to the bottom, and it stays pinned
 * there as long as the user hasn't scrolled up. Once they do, new items no
 * longer yank the view down — the caller renders a "N mới" pill (see
 * `ScrollToBottomPill`) using `pendingCount`/`pinned`, and `scrollToBottom`
 * both jumps down and re-arms pinning.
 *
 * `count` is the length of the list driving the pane (turns, log lines, …);
 * bump it every time a new item is appended. Attach both `ref` (scroll
 * container) and `contentRef` (its content wrapper) — the latter is what lets
 * a pinned view follow content that grows taller without `count` changing.
 */
export function useStickToBottom<T extends HTMLElement, C extends HTMLElement = HTMLElement>(
  count: number,
): StickToBottom<T, C> {
  const ref = useRef<T>(null);
  const contentRef = useRef<C>(null);
  const pinnedRef = useRef(true);
  const [pinned, setPinned] = useState(true);
  const [pendingCount, setPendingCount] = useState(0);
  const lastCount = useRef(count);
  const mounted = useRef(false);

  const scrollToBottom = useCallback((options: { smooth?: boolean } = {}) => {
    const element = ref.current;
    if (!element) return;
    element.scrollTo({
      top: element.scrollHeight,
      behavior: options.smooth && !prefersReducedMotion() ? "smooth" : "auto",
    });
    pinnedRef.current = true;
    setPinned(true);
    setPendingCount(0);
  }, []);

  const onScroll = useCallback(() => {
    const element = ref.current;
    if (!element) return;
    const atBottom =
      element.scrollHeight - element.scrollTop - element.clientHeight < BOTTOM_THRESHOLD_PX;
    pinnedRef.current = atBottom;
    setPinned(atBottom);
    if (atBottom) setPendingCount(0);
  }, []);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      const element = ref.current;
      if (!element) return;
      if (event.key === "End") {
        event.preventDefault();
        scrollToBottom({ smooth: true });
      } else if (event.key === "Home") {
        event.preventDefault();
        element.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
        pinnedRef.current = false;
        setPinned(false);
      }
    },
    [scrollToBottom],
  );

  // Open scrolled to the bottom; on every later render where `count` grew,
  // either follow it down (pinned) or surface the pill (scrolled up).
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (!mounted.current) {
      mounted.current = true;
      element.scrollTop = element.scrollHeight;
      lastCount.current = count;
      return;
    }
    const added = count - lastCount.current;
    lastCount.current = count;
    if (added <= 0) return;
    if (pinnedRef.current) {
      element.scrollTop = element.scrollHeight;
    } else {
      setPendingCount((pending) => pending + added);
    }
  }, [count]);

  // Some panes grow taller without a new item — an image or diff finishing
  // layout inside an existing entry. Observe the content wrapper's own
  // (unconstrained) height, not the scroll container's fixed-height box,
  // which never resizes no matter how tall its content gets.
  useEffect(() => {
    const content = contentRef.current;
    const element = ref.current;
    if (!content || !element || typeof ResizeObserver === "undefined") return;
    let lastHeight = content.scrollHeight;
    const observer = new ResizeObserver(() => {
      const height = content.scrollHeight;
      if (height === lastHeight) return;
      lastHeight = height;
      if (pinnedRef.current) element.scrollTop = element.scrollHeight;
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, []);

  return { ref, contentRef, pinned, pendingCount, onScroll, onKeyDown, scrollToBottom };
}
