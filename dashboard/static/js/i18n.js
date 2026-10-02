"use strict";
(() => {
  const catalog = JSON.parse(document.getElementById("i18n-catalog").textContent);
  const t = (text, values = {}) => {
    const translated = Object.prototype.hasOwnProperty.call(catalog, text) ? catalog[text] : text;
    return translated.replace(/\{(\w+)\}/g, (match, key) =>
      Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match);
  };
  const message = text => {
    let match;
    if ((match = /^(.+): unavailable \/ partial data(; showing last valid data)?$/.exec(text))) {
      return t("{file}: unavailable / partial data" + (match[2] ? "; showing last valid data" : ""), {file: match[1]});
    }
    if ((match = /^Submission (.+): code hash mismatch$/.exec(text))) {
      return t("Submission {id}: code hash mismatch", {id: match[1]});
    }
    if ((match = /^(\d+) \/ (\d+) passed$/.exec(text))) {
      return t("{passed} / {total} passed", {passed: match[1], total: match[2]});
    }
    return t(text);
  };
  window.CodeHarnessI18n = Object.freeze({t, message});
  // Preserve the selected detail section as well as the server's path and filters.
  const updateLinks = () => document.querySelectorAll("[data-language]").forEach(link => {
    const url = new URL(link.href);
    url.searchParams.set("next", location.pathname + location.search + location.hash);
    link.href = url.href;
  });
  updateLinks();
  window.addEventListener("hashchange", updateLinks);
  document.querySelectorAll("[data-language]").forEach(link => link.addEventListener("click", updateLinks));
})();
