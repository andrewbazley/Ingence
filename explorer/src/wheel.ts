// The wheel over a drawing, the same in every mode and on the Dashboard's own pages:
//
//   Ctrl + wheel     zooms around the pointer; a trackpad pinch arrives this way
//   sideways wheel   pans: a two-finger swipe left or right, or Shift + wheel
//   any other wheel  is left to the page, so a two-finger scroll moves past the drawing
//
// React's onWheel listener is passive, so it cannot keep the page still while a drawing zooms. The
// listener here is the element's own, not passive, and it holds the page still only when the
// drawing used the wheel.

import {useEffect, useRef, type RefObject} from 'react';

/** What a wheel event asks of a drawing: to zoom, to pan, or nothing (the page scrolls). */
export type WheelIntent = 'zoom' | 'pan' | 'page';

export function wheelIntent(event: WheelEvent): WheelIntent {
  if (event.ctrlKey) return 'zoom';
  if (event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY)) return 'pan';
  return 'page';
}

/** A wheel delta in pixels, whatever unit the browser gave it in (lines or pages). */
export function wheelPixels(delta: number, mode: number): number {
  if (mode === 1) return delta * 16;
  if (mode === 2) return delta * 800;
  return delta;
}

/** How much one zooming event magnifies: above 1 zooms in. A notch of a mouse wheel is about a
 * quarter; a pinch comes in many small steps. */
export function zoomStep(event: WheelEvent): number {
  return Math.exp(-Math.max(-0.25, Math.min(0.25, wheelPixels(event.deltaY, event.deltaMode) * 0.01)));
}

/** How far a panning event moves, in pixels, positive to the right. With Shift some browsers turn
 * the upright delta sideways and others leave it upright, so the larger one is taken. */
export function panPixels(event: WheelEvent): number {
  const sideways = Math.abs(event.deltaX) > Math.abs(event.deltaY);
  return wheelPixels(sideways ? event.deltaX : event.deltaY, event.deltaMode);
}

/**
 * Listen to the wheel over `target` (an element, or a ref to one that is always drawn). The handler
 * is called only for a zoom or a pan, and returns whether it used the event: only then is the
 * page held still.
 */
export function useWheel(
  target: HTMLElement | null | RefObject<HTMLElement | null>,
  handler: (event: WheelEvent, intent: 'zoom' | 'pan') => boolean,
): void {
  const latest = useRef(handler);
  useEffect(() => {
    latest.current = handler;
  });
  useEffect(() => {
    const element = target instanceof HTMLElement ? target : target?.current ?? null;
    if (!element) return undefined;
    const listener = (event: WheelEvent) => {
      const intent = wheelIntent(event);
      if (intent === 'page') return;
      if (latest.current(event, intent)) event.preventDefault();
    };
    element.addEventListener('wheel', listener, {passive: false});
    return () => element.removeEventListener('wheel', listener);
  }, [target]);
}
