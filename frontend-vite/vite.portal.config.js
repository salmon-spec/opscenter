import {defineConfig} from 'vite'
import vue from '@vitejs/plugin-vue'
import {fileURLToPath} from 'node:url'
export default defineConfig({
 plugins:[{name:'portal-api',enforce:'pre',resolveId(source,importer){if(source==='../api'&&importer?.includes('/src/views/'))return fileURLToPath(new URL('./src/portal-api.js',import.meta.url));}},vue()],
 build:{outDir:'dist-portal',emptyOutDir:true,cssCodeSplit:false,lib:{entry:'src/portal-life.js',name:'PortalLife',formats:['iife'],fileName:()=> 'app.js'},rollupOptions:{output:{inlineDynamicImports:true}}},
});
