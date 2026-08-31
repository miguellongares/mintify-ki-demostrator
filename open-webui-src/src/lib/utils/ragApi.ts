// Single source of truth for the browser -> rag-backend base URL.
//
// The rag-backend is called DIRECTLY from the browser (Bewertung panel, personal
// files), bypassing the OWUI backend, so the URL depends on how the page is served:
//
//   1. Vite dev server  -> relative `/rag-api`, handled by the proxy rule in
//      vite.config.ts. Relative means it also works when the page is opened from
//      a phone on the LAN (http://<mac-ip>:5173), which a hardcoded `localhost`
//      would not.
//   2. Baked Docker build on localhost:3000 (no Caddy) -> the published port 8088.
//   3. Production behind Caddy -> `/rag-api/*`, stripped and proxied to 8088.
//
// Never hardcode a port at a call site — extend this helper instead.
export const ragApiBase = (): string => {
	if (import.meta.env.DEV) {
		return '/rag-api';
	}

	const hostname = window.location.hostname;
	if (hostname === 'localhost' || hostname === '127.0.0.1') {
		return 'http://localhost:8088';
	}

	return `${window.location.origin}/rag-api`;
};
