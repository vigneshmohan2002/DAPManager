// @ts-check

// Browser sessions authenticate through an HttpOnly same-site cookie, so
// media elements and fetch() work without exposing the token to JavaScript.
window.dapApiUrl = function dapApiUrl(url) { return url; };
