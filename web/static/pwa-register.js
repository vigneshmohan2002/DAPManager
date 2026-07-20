// @ts-check
(function registerDAPManagerServiceWorker() {
    "use strict";

    if (!("serviceWorker" in navigator)) {
        return;
    }

    window.addEventListener("load", function () {
        navigator.serviceWorker.register("/service-worker.js", {
            scope: "/",
            updateViaCache: "none",
        }).catch(function (error) {
            // The UI remains fully functional on plain HTTP hosts where the
            // browser correctly declines service-worker registration.
            console.warn("DAPManager service worker was not registered", error);
        });
    });
}());
