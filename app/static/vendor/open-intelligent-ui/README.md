# OpenIntelligentUI design system

Vendored theme, SVG classes, and form styles from
https://github.com/CopilotKit/OpenIntelligentUI
commit `f6e4388b26a64b9a0714943b08a1ce622b924eec`, `packages/design-system/src/index.ts`.

MIT licensed; see LICENSE. Tomo adapts the sandbox document contract in
`app/static/js/intelligent_ui.js` to its existing render_ui tool and SSE history.

The trip animation controller is also vendored from
`apps/app/src/components/generative-ui/open-generative-ui/trip-animator.ts`
at the same revision. Type annotations are removed; its runtime behavior is
preserved. The sandbox exposes it as `window.createTripAnimator`.
