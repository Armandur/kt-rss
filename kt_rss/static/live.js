/* Liveuppdatering av startsidan: frågar med jämna mellanrum /latest om det
   kommit artiklar nyare än den översta i listan. Om så visas en banner
   över listan - ett klick laddar om sidan, varpå newsince.js märker de
   nya artiklarna med "Ny"-pill. Vanilla JS, inget ramverk. */
(function () {
  "use strict";

  if (location.pathname !== "/") return;
  var list = document.querySelector(".articles");
  if (!list) return;
  var first = list.querySelector("article[data-published]");
  if (!first) return;

  var since = first.dataset.published;
  var banner = null;

  function showBanner(count) {
    if (!banner) {
      banner = document.createElement("button");
      banner.type = "button";
      banner.className = "live-banner";
      banner.addEventListener("click", function () {
        location.reload();
      });
      list.parentNode.insertBefore(banner, list);
    }
    var ord = count === 1 ? "ny artikel" : "nya artiklar";
    banner.textContent = count + " " + ord + " - visa";
  }

  function check() {
    fetch("/latest?after=" + encodeURIComponent(since))
      .then(function (resp) { return resp.json(); })
      .then(function (data) {
        if (data.count > 0) showBanner(data.count);
      })
      .catch(function () {});
  }

  setInterval(check, 120000);
})();
