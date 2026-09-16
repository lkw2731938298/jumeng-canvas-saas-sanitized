"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

export type HomeActivitySlide = {
  id: string;
  title: string;
  subtitle: string;
  image: string;
};

type Props = {
  items: HomeActivitySlide[];
  onOpen: () => void;
  /** 自动轮播间隔（毫秒） */
  intervalMs?: number;
};

/**
 * 首页活动区：16:9 高端轮播。
 * 封面铺满、底部渐变文案、圆点指示；悬停暂停自动切换。
 */
export function HomeActivityCarousel({
  items,
  onOpen,
  intervalMs = 5200,
}: Props) {
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const count = items.length;

  useEffect(() => {
    setIndex(0);
  }, [count]);

  useEffect(() => {
    if (count <= 1 || paused) return;
    const timer = window.setInterval(() => {
      setIndex((i) => (i + 1) % count);
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [count, paused, intervalMs]);

  if (count === 0) return null;

  const go = (next: number) => {
    setIndex(((next % count) + count) % count);
  };

  const current = items[index] ?? items[0];

  return (
    <div
      className="activity-carousel"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <button
        type="button"
        className="activity-carousel-stage"
        onClick={onOpen}
        aria-label={`活动：${current.title}`}
      >
        {items.map((item, i) => (
          <img
            key={item.id}
            className={`activity-carousel-slide${i === index ? " is-active" : ""}`}
            src={item.image}
            alt=""
            draggable={false}
          />
        ))}
        <div className="activity-carousel-veil" aria-hidden="true" />
        <div className="activity-carousel-copy">
          <span className="activity-carousel-kicker">限时活动</span>
          <strong>{current.title}</strong>
          <span>{current.subtitle}</span>
        </div>
      </button>

      {count > 1 ? (
        <>
          <button
            type="button"
            className="activity-carousel-nav is-prev"
            aria-label="上一项活动"
            onClick={(e) => {
              e.stopPropagation();
              go(index - 1);
            }}
          >
            <ChevronLeft size={18} strokeWidth={2} />
          </button>
          <button
            type="button"
            className="activity-carousel-nav is-next"
            aria-label="下一项活动"
            onClick={(e) => {
              e.stopPropagation();
              go(index + 1);
            }}
          >
            <ChevronRight size={18} strokeWidth={2} />
          </button>
          <div className="activity-carousel-dots" role="tablist" aria-label="活动轮播">
            {items.map((item, i) => (
              <button
                type="button"
                key={item.id}
                role="tab"
                aria-selected={i === index}
                aria-label={`第 ${i + 1} 项`}
                className={i === index ? "is-active" : undefined}
                onClick={(e) => {
                  e.stopPropagation();
                  go(i);
                }}
              />
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}
