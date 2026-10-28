// Lineage's layout, asked of a Web Worker so a large lineage never stalls the page; where no
// worker can start, the same layout runs here, loaded only then.

import type {LayoutRequest, LayoutResult} from './layoutCore';

export type {LayoutEdge, LayoutNode, LayoutRequest, LayoutResult, Placed} from './layoutCore';

let worker: Worker | null = null;
let serial = 0;
interface Waiting {
  request: LayoutRequest;
  resolve(result: LayoutResult): void;
  reject(error: unknown): void;
}
const waiting = new Map<number, Waiting>();

/** Lay a lineage out in the worker, or here where the page can start none. */
export function layoutInWorker(request: LayoutRequest): Promise<LayoutResult> {
  if (typeof Worker === 'undefined') return import('./layoutCore').then((core) => core.computeLayout(request));
  if (!worker) {
    try {
      worker = new Worker(new URL('./layout.worker.ts', import.meta.url), {type: 'module'});
      worker.onmessage = (event: MessageEvent<{serial: number; result: LayoutResult}>) => {
        const held = waiting.get(event.data.serial);
        waiting.delete(event.data.serial);
        held?.resolve(event.data.result);
      };
      // A worker that fails never leaves a layout waiting. Each layout that was waiting is laid
      // out here instead, and where that fails too the promise says so.
      worker.onerror = (event) => {
        event.preventDefault();
        worker = null;
        const pending = [...waiting.values()];
        waiting.clear();
        for (const held of pending) {
          import('./layoutCore')
            .then((core) => held.resolve(core.computeLayout(held.request)))
            .catch((error: unknown) => held.reject(error));
        }
      };
    } catch {
      return import('./layoutCore').then((core) => core.computeLayout(request));
    }
  }
  serial += 1;
  const mine = serial;
  return new Promise((resolve, reject) => {
    waiting.set(mine, {request, resolve, reject});
    worker?.postMessage({serial: mine, request});
  });
}
