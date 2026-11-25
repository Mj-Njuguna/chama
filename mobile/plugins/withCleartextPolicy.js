/**
 * Config plugin: the MAIN manifest (used by release builds) explicitly refuses cleartext HTTP.
 *
 * Android only defaults to refusing cleartext from API 28; SDK 58 supports API 24+, so the
 * attribute must be explicit. Debug variants keep HTTP for the local backend and Metro through
 * the template's src/debug overlay (`usesCleartextTraffic="true"` with `tools:replace`).
 * Proven on the merged manifests by scripts/check-android-manifests.js in CI.
 */
const { withAndroidManifest } = require('expo/config-plugins');

module.exports = function withCleartextPolicy(config) {
  return withAndroidManifest(config, (mod) => {
    const application = mod.modResults.manifest.application?.[0];
    if (application === undefined) {
      throw new Error('withCleartextPolicy: AndroidManifest.xml has no <application>');
    }
    application.$['android:usesCleartextTraffic'] = 'false';
    return mod;
  });
};
