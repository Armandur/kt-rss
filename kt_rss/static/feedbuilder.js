/* Bygg-en-tagg-feed-sidan (/tags). Sökrutan filtrerar tagglistan, och
   den genererade feed-URL:en uppdateras live när taggar eller läge ändras.
   Alla taggar renderas direkt - hundratals <label> klarar DOM:en; sökrutan
   sköter användbarheten. Vanilla JS, inget ramverk. */
(function () {
  "use strict";

  var form = document.getElementById("feedbuilder");
  if (!form) return;
  var search = document.getElementById("tag-search");
  var result = document.getElementById("fb-result");
  var urlLink = document.getElementById("fb-url");

  function updateUrl() {
    var checked = form.querySelectorAll('input[name="t"]:checked');
    var tags = Array.prototype.map.call(checked, function (c) {
      return encodeURIComponent(c.value);
    });
    if (tags.length === 0) {
      result.hidden = true;
      return;
    }
    var mode = form.querySelector('input[name="mode"]:checked').value;
    var url = location.origin + "/feed/tags.xml?t=" + tags.join(",") +
      "&mode=" + mode;
    urlLink.textContent = url;
    urlLink.href = url;
    result.hidden = false;
  }

  function filterTags() {
    var q = search.value.trim().toLowerCase();
    form.querySelectorAll(".fb-tag").forEach(function (label) {
      var name = label.querySelector(".fb-tag-name").textContent.toLowerCase();
      label.hidden = q !== "" && name.indexOf(q) === -1;
    });
  }

  form.addEventListener("change", updateUrl);
  search.addEventListener("input", filterTags);
})();
