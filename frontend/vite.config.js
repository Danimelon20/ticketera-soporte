// Vite construye las islas de React en un solo archivo de nombre fijo: dist/islands.js.
// La página sigue siendo static/index.html (no hay index.html de Vite); Docker copia dist/ a static/islands/.
// El navegador no se queda con una versión vieja porque FastAPI responde los .js con «Cache-Control: no-cache»
// (revalida con ETag en cada carga), así que el nombre no necesita hash.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
    plugins: [react()],
    base: '/islands/',
    build: {
        outDir: 'dist',
        emptyOutDir: true,
        // Vite 8 usa Rolldown: rolldownOptions (rollupOptions es un alias obsoleto)
        rolldownOptions: {
            input: 'src/main.jsx',
            output: {
                entryFileNames: 'islands.js',
                chunkFileNames: 'islands-[name].js',
                assetFileNames: 'islands-[name][extname]',
            },
        },
    },
});
