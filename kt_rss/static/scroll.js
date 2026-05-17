/* Infinite scroll för artikellistorna. Laddar nästa batch som ett
   HTML-fragment (?partial=1) när sentineln närmar sig viewporten.
   Vanilla JS, inget ramverk - laddas bara på sidor som har fler sidor. */
(function () {
  "use strict";

  var sentinel = document.querySelector(".scroll-sentinel");
  var articles = document.querySelector(".articles");
  if (!sentinel || !articles) return;

  var loading = false;

  /* En batch kan börja mitt i en dag, så fragmentet får en dag-rubrik som
     upprepar den föregående. Ta bort sådana dubbletter. */
  function dedupeDaySeparators() {
    var prev = null;
    articles.querySelectorAll(".day-sep").forEach(function (el) {
      var text = el.textContent.trim();
      if (prev !== null && text === prev) {
        el.remove();
      } else {
        prev = text;
      }
    });
  }

  function loadNext() {
    if (loading) return;
    var next = parseInt(sentinel.dataset.nextPage, 10);
    var total = parseInt(sentinel.dataset.totalPages, 10);
    if (!next || next > total) {
      sentinel.remove();
      return;
    }
    loading = true;
    // base kan redan ha en query-sträng (t.ex. /search?q=...).
    var base = sentinel.dataset.base;
    var sep = base.indexOf("?") === -1 ? "?" : "&";
    var url = base + sep + "page=" + next + "&partial=1";
    fetch(url)
      .then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.text();
      })
      .then(function (html) {
        articles.insertAdjacentHTML("beforeend", html);
        dedupeDaySeparators();
        if (next >= total) {
          sentinel.remove();
        } else {
          sentinel.dataset.nextPage = next + 1;
        }
      })
      .catch(function () {
        /* Vid fel: sluta försöka i stället för att loopa. */
        sentinel.remove();
      })
      .finally(function () {
        loading = false;
      });
  }

  var observer = new IntersectionObserver(
    function (entries) {
      if (entries.some(function (e) { return e.isIntersecting; })) {
        loadNext();
      }
    },
    { rootMargin: "400px" }
  );
  observer.observe(sentinel);
})();
