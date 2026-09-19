import { useState } from 'react'
import { mediaFor } from './products'

export default function Thumb({ productId, size = 56, radius = 12 }) {
  const media = mediaFor(productId)
  const [failed, setFailed] = useState(!media.photo)

  const style = { width: size, height: size, borderRadius: radius }

  if (failed) {
    return (
      <div className="thumb fallback" style={{
        ...style,
        background: `linear-gradient(135deg, ${media.tint}33, ${media.tint}14)`,
        border: `1px solid ${media.tint}40`,
        fontSize: size * 0.45,
      }}>
        {media.emoji}
      </div>
    )
  }

  return (
    <img className="thumb" src={media.photo} alt="" style={style}
         onError={() => setFailed(true)} loading="lazy" />
  )
}
