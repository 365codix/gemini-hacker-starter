'use client';

import { useState } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import { ImagesIcon, XIcon, ChevronLeftIcon } from 'lucide-react';
import { type GeneratedImage, useGeneratedImages } from '@/hooks/useGeneratedImages';
import { cn } from '@/lib/shadcn/utils';

const MotionPanel = motion.create('div');
const MotionOverlay = motion.create('div');

interface ThumbnailProps {
  image: GeneratedImage;
  onClick: () => void;
}

function Thumbnail({ image, onClick }: ThumbnailProps) {
  return (
    <button
      onClick={onClick}
      className="group relative aspect-square overflow-hidden rounded-lg border border-input/50 bg-muted transition-all hover:border-foreground/20 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={image.imageUrl}
        alt={image.prompt}
        className="h-full w-full object-cover transition-transform duration-200 group-hover:scale-105"
      />
    </button>
  );
}

interface FullImageViewProps {
  image: GeneratedImage;
  onBack: () => void;
}

function FullImageView({ image, onBack }: FullImageViewProps) {
  return (
    <MotionPanel
      key="full"
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 24 }}
      transition={{ duration: 0.2, ease: 'easeOut' }}
      className="flex h-full flex-col"
    >
      <button
        onClick={onBack}
        className="mb-3 flex items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronLeftIcon className="size-4" />
        All images
      </button>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={image.imageUrl}
        alt={image.prompt}
        className="w-full rounded-xl object-contain"
      />
      {image.prompt && (
        <p className="mt-3 text-sm text-muted-foreground">{image.prompt}</p>
      )}
    </MotionPanel>
  );
}

/**
 * Floating gallery button + slide-in panel for all agent-generated images.
 * The button appears once at least one image has been generated.
 */
export function ImageGallery() {
  const images = useGeneratedImages();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<GeneratedImage | null>(null);

  if (images.length === 0) return null;

  return (
    <>
      {/* Floating trigger button */}
      <AnimatePresence>
        {!open && (
          <MotionPanel
            key="trigger"
            initial={{ opacity: 0, scale: 0.85 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.85 }}
            transition={{ duration: 0.2, ease: 'easeOut' }}
            className="fixed bottom-36 right-4 z-40 md:bottom-44"
          >
            <button
              onClick={() => setOpen(true)}
              aria-label="View generated images"
              className={cn(
                'relative flex items-center justify-center rounded-full p-3',
                'bg-background border border-input/50 shadow-lg',
                'transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'
              )}
            >
              <ImagesIcon className="size-5 text-foreground" />
              <span className="absolute -top-1.5 -right-1.5 flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-bold text-primary-foreground">
                {images.length}
              </span>
            </button>
          </MotionPanel>
        )}
      </AnimatePresence>

      {/* Gallery panel + backdrop */}
      <AnimatePresence>
        {open && (
          <>
            <MotionOverlay
              key="overlay"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
              onClick={() => {
                setSelected(null);
                setOpen(false);
              }}
            />

            <MotionPanel
              key="panel"
              initial={{ x: '100%' }}
              animate={{ x: 0 }}
              exit={{ x: '100%' }}
              transition={{ duration: 0.25, ease: 'easeOut' }}
              className="fixed top-0 bottom-0 right-0 z-[60] flex w-80 flex-col overflow-hidden border-l border-input/50 bg-background shadow-2xl md:top-16"
            >
              {/* Header */}
              <div className="flex items-center justify-between border-b border-input/50 px-4 py-3">
                <div>
                  <h2 className="text-sm font-semibold">Generated images</h2>
                  <p className="text-xs text-muted-foreground">
                    {images.length} {images.length === 1 ? 'image' : 'images'}
                  </p>
                </div>
                <button
                  onClick={() => {
                    setSelected(null);
                    setOpen(false);
                  }}
                  aria-label="Close gallery"
                  className="rounded-full p-1.5 transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <XIcon className="size-4 text-muted-foreground" />
                </button>
              </div>

              {/* Content */}
              <div className="flex-1 overflow-y-auto p-4">
                <AnimatePresence mode="wait">
                  {selected ? (
                    <FullImageView
                      key={selected.id}
                      image={selected}
                      onBack={() => setSelected(null)}
                    />
                  ) : (
                    <MotionPanel
                      key="grid"
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.15 }}
                      className="grid grid-cols-2 gap-2"
                    >
                      {[...images].reverse().map((img) => (
                        <Thumbnail key={img.id} image={img} onClick={() => setSelected(img)} />
                      ))}
                    </MotionPanel>
                  )}
                </AnimatePresence>
              </div>
            </MotionPanel>
          </>
        )}
      </AnimatePresence>
    </>
  );
}
