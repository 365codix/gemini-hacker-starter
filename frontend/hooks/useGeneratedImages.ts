'use client';

import { useEffect, useRef, useState } from 'react';
import { useRoomContext } from '@livekit/components-react';

export interface GeneratedImage {
  id: string;
  imageUrl: string;
  mimeType: string;
  prompt: string;
  timestamp: number;
}

/**
 * Subscribes to "generated-image" byte streams published by the agent
 * and returns the list of received images as blob URLs.
 *
 * HACK HERE: extend this hook to persist images, add a gallery, etc.
 */
export function useGeneratedImages(): GeneratedImage[] {
  const room = useRoomContext();
  const [images, setImages] = useState<GeneratedImage[]>([]);
  // Track registered state to avoid double-registering on re-renders
  const registeredRef = useRef(false);

  useEffect(() => {
    if (registeredRef.current) return;
    registeredRef.current = true;

    room.registerByteStreamHandler('generated-image', async (reader) => {
      try {
        const chunks = await reader.readAll();
        const mimeType = reader.info.mimeType || 'image/png';
        const blob = new Blob(chunks, { type: mimeType });
        const imageUrl = URL.createObjectURL(blob);

        const image: GeneratedImage = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
          imageUrl,
          mimeType,
          prompt: reader.info.attributes?.prompt ?? '',
          timestamp: Date.now(),
        };

        setImages((prev) => [...prev, image]);
      } catch (err) {
        console.error('Failed to receive generated-image byte stream:', err);
      }
    });

    return () => {
      room.unregisterByteStreamHandler('generated-image');
      registeredRef.current = false;
    };
  }, [room]);

  return images;
}
