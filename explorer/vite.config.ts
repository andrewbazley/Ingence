// The explorer's build. One entry, fixed names, no hashes: index.html loads
// /explorer/explorer.js by name, and the engine serves whatever sits in src/ingence/web/explorer/.
// Nothing is fetched at run time: every dependency is bundled, and no font or CDN is named.
import react from '@vitejs/plugin-react';
import {defineConfig} from 'vite';

export default defineConfig({
  plugins: [react()],
  base: '/explorer/',
  build: {
    outDir: '../src/ingence/web/explorer',
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: false,
    cssCodeSplit: false,
    modulePreload: false,
    reportCompressedSize: false,
    rollupOptions: {
      input: 'src/main.tsx',
      output: {
        entryFileNames: 'explorer.js',
        chunkFileNames: 'explorer-[name].js',
        assetFileNames: 'explorer[extname]',
      },
    },
  },
  worker: {
    format: 'es',
    rollupOptions: {output: {entryFileNames: 'explorer-worker-[name].js'}},
  },
});
