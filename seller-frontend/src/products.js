// Product imagery. Photos load from Unsplash; if anything fails to load the
// component falls back to a coloured tile with the glyph, so the UI never
// shows a broken image.
export const PRODUCT_MEDIA = {
  5001: { emoji: '🥭', tint: '#f5a524',
          photo: 'https://images.unsplash.com/photo-1553279768-865429fa0078?w=400&q=70' },
  5002: { emoji: '❄️', tint: '#4f8cff',
          photo: 'https://images.unsplash.com/photo-1631545806609-cd5e1e9e4b2f?w=400&q=70' },
  5003: { emoji: '🧥', tint: '#8b6cf0',
          photo: 'https://images.unsplash.com/photo-1544022613-e87ca75a784a?w=400&q=70' },
  5004: { emoji: '🧶', tint: '#e0709a',
          photo: 'https://images.unsplash.com/photo-1576871337622-98d48d1cf531?w=400&q=70' },
  5005: { emoji: '🍞', tint: '#d99a4e',
          photo: 'https://images.unsplash.com/photo-1509440159596-0249088772ff?w=400&q=70' },
  5006: { emoji: '🫒', tint: '#5ec269',
          photo: 'https://images.unsplash.com/photo-1474979266404-7eaacbcd87c5?w=400&q=70' },
}

export const mediaFor = (id) =>
  PRODUCT_MEDIA[id] || { emoji: '📦', tint: '#7a8394', photo: null }
