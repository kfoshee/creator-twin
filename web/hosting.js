// Hosting environment detection (vanilla equivalent of src/lib/hosting.ts).
// No secrets here — purely informational.
function detectHosting() {
  var h = location.hostname;
  var isLocal = h === "localhost" || h === "127.0.0.1" || h === "";
  var label = "unknown";
  if (isLocal) label = "localhost";
  else if (h.endsWith("github.io")) label = "GitHub Pages";
  else if (h.endsWith("vercel.app")) label = "Vercel";
  else if (h.endsWith("onrender.com")) label = "Render";
  else if (h.endsWith("railway.app")) label = "Railway";
  var cfg = window.CT_CONFIG || {};
  return {
    environmentLabel: label,
    isLocal: isLocal,
    isStaticDemo: !!cfg.DEMO_MODE,
    hostname: h,
  };
}
window.detectHosting = detectHosting;
