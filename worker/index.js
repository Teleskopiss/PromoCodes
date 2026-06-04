/**
 * Cloudflare Worker: dispatch-proxy
 *
 * Forwards a POST /dispatch request to GitHub Actions workflow_dispatch.
 * Stores the PAT as a Worker secret: GH_TOKEN
 *
 * Deploy:
 *   1. npx wrangler secret put GH_TOKEN   (paste your fine-grained PAT)
 *   2. npx wrangler deploy
 *   3. Copy the worker URL into index.html  DISPATCH_PROXY_URL
 *
 * The PAT needs: repo -> Actions -> Read and Write (workflow)
 */

export default {
  async fetch(request, env) {
    // Allow CORS from your GitHub Pages domain
    const origin = request.headers.get('Origin') || '';
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders });
    }

    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405, headers: corsHeaders });
    }

    const GH_OWNER = 'Teleskopiss';
    const GH_REPO  = 'PromoCodes';
    const WORKFLOW = 'scrape.yml';

    const resp = await fetch(
      `https://api.github.com/repos/${GH_OWNER}/${GH_REPO}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${env.GH_TOKEN}`,
          'Accept': 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
          'Content-Type': 'application/json',
          'User-Agent': 'PromoRadar-Proxy/1.0',
        },
        body: JSON.stringify({ ref: 'main' }),
      }
    );

    // 204 = dispatched OK, anything else = error
    const body = resp.status === 204 ? '' : await resp.text();
    return new Response(body, {
      status: resp.status,
      headers: { ...corsHeaders, 'Content-Type': 'application/json' },
    });
  },
};
