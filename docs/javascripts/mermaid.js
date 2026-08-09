(function () {
  function renderMermaid() {
    if (!window.mermaid) {
      return;
    }

    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: document.body.getAttribute("data-md-color-scheme") === "slate" ? "dark" : "default",
    });

    document.querySelectorAll(".mermaid-diagram:not([data-mermaid-rendered])").forEach(async function (container, index) {
      const source = container.textContent.trim();
      if (!source) {
        return;
      }

      container.dataset.mermaidRendered = "true";
      try {
        const result = await window.mermaid.render("mermaid-render-" + index, source);
        container.innerHTML = result.svg;
      } catch (error) {
        console.warn("Mermaid diagram rendering failed", error);
        delete container.dataset.mermaidRendered;
      }
    });
  }

  if (typeof document$ !== "undefined") {
    document$.subscribe(renderMermaid);
  } else {
    document.addEventListener("DOMContentLoaded", renderMermaid);
  }
})();