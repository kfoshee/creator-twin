// Frontend config. The GitHub Pages build overwrites this with DEMO_MODE=true.
// NEVER put private API keys here — this file ships to the browser.
window.CT_CONFIG = {
  DEMO_MODE: false,          // true = no backend; loads demo/creator_demo.json
  USE_MOCK_DATA: false,
  API_BASE_URL: "",          // e.g. "https://your-backend.onrender.com" in production
  PUBLIC_BASE_PATH: "",
};
