import '@testing-library/jest-dom/vitest'

/**
 * jsdom n'implémente ni `URL.createObjectURL` ni le contexte WebGL dont MapLibre
 * a besoin au chargement du module.
 *
 * Ces stubs ne simulent pas la carte — ils permettent seulement d'importer les
 * pages qui en contiennent une. Ce qui touche réellement à la carte se vérifie
 * dans un vrai navigateur, et le registre d'honnêteté le dit plutôt que de
 * laisser croire qu'un test jsdom couvre le rendu cartographique.
 */
if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = () => 'blob:test'
  URL.revokeObjectURL = () => undefined
}

if (typeof HTMLCanvasElement !== 'undefined') {
  HTMLCanvasElement.prototype.getContext = (() => null) as never
}
