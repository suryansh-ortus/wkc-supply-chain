/**
 * The engines have internal codenames (stn / ltn). Nobody using the console
 * should ever see those — this maps them to what they actually do.
 */

export const ENGINES = {
  stn: {
    name: 'Restock Agent',
    short: 'Restock',
    icon: '⚡',
    blurb: 'Reads our own sales, stock and shelf life',
  },
  ltn: {
    name: 'Market Agent',
    short: 'Market',
    icon: '🌐',
    blurb: 'Researches weather, prices and demand on the web',
  },
}

const FALLBACK = {
  name: 'Stock Watch',
  short: 'Stock Watch',
  icon: '🛡️',
  blurb: 'Automatic daily shortage check',
}

export const engineOf = (key) =>
  ENGINES[String(key || '').toLowerCase()] || FALLBACK
