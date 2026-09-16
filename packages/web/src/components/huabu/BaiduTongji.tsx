"use client";

import { useEffect } from "react";

const BAIDU_HM_ID = "";
/** 仅生产用户站加载百度统计；开源副本默认不注入 */
const BAIDU_HOSTS = new Set(["www.example.com"]);

/**
 * 百度统计（hm.js）。无 HM ID 时不加载；有 ID 时仅配置主机名注入。
 */
export function BaiduTongji() {
  useEffect(() => {
    if (!BAIDU_HM_ID) return;
    if (!BAIDU_HOSTS.has(window.location.hostname)) return;
    const src = `https://hm.baidu.com/hm.js?${BAIDU_HM_ID}`;
    if (document.querySelector(`script[src="${src}"]`)) return;
    const w = window as Window & { _hmt?: unknown[] };
    w._hmt = w._hmt || [];
    const hm = document.createElement("script");
    hm.async = true;
    hm.src = src;
    document.body.appendChild(hm);
  }, []);
  return null;
}
