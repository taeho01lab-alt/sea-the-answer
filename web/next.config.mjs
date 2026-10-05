import {dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const api = process.env.HAEDAP_API_URL || 'http://127.0.0.1:8000';
export default {
  turbopack: {root: dirname(fileURLToPath(import.meta.url))},
  devIndicators: false,
  poweredByHeader: false,
  async rewrites() { return [{source:'/api/:path*',destination:`${api}/api/:path*`}]; },
  async headers() { return [{source:'/:path*',headers:[
    {key:'X-Content-Type-Options',value:'nosniff'},
    {key:'Referrer-Policy',value:'same-origin'},
    {key:'X-Frame-Options',value:'DENY'}
  ]}]; }
};
