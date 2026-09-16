"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  cstDateEndExclusiveToUtcIso,
  cstDateStartToUtcIso,
} from "@/lib/admin/adminJobQuery";
import { normalizeUserNo } from "@/lib/admin/displayIds";
import { listAdminRechargeOrders } from "@/lib/api/admin";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import { cn } from "@/lib/utils";

function formatFenToYuan(fen: number) {
  if (fen <= 0) return "—";
  return `¥${(fen / 100).toFixed(fen % 100 === 0 ? 0 : 2)}`;
}

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  completed: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  failed: "bg-red-500/15 text-red-700 dark:text-red-300",
  expired: "bg-slate-500/15 text-slate-600 dark:text-slate-300",
};

export default function AdminRechargeOrdersPage() {
  const [page, setPage] = useState(1);
  const [userId, setUserId] = useState("");
  const [orderType, setOrderType] = useState("");
  const [status, setStatus] = useState("");
  const [paymentChannel, setPaymentChannel] = useState("");
  const [outTradeNo, setOutTradeNo] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const pageSize = 20;

  const createdFrom = dateFrom ? cstDateStartToUtcIso(dateFrom) : undefined;
  const createdTo = dateTo ? cstDateEndExclusiveToUtcIso(dateTo) : undefined;

  const filterParams = useMemo(
    () => ({
      page,
      pageSize,
      userId: userId.trim() || undefined,
      orderType: orderType || undefined,
      status: status || undefined,
      paymentChannel: paymentChannel || undefined,
      outTradeNo: outTradeNo.trim() || undefined,
      createdFrom,
      createdTo,
    }),
    [page, pageSize, userId, orderType, status, paymentChannel, outTradeNo, createdFrom, createdTo]
  );

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["admin", "recharge-orders", filterParams],
    queryFn: () => listAdminRechargeOrders(filterParams),
  });

  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / pageSize));

  return (
    <div>
      <AdminHeader
        title="充值记录"
        description="全站算力充值与会员开通支付订单（支付宝扫码）。"
      />

      <div className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-border bg-muted/20 p-4">
        <div className="w-[200px] space-y-1">
          <label className="text-xs text-muted-foreground">用户</label>
          <Input
            value={userId}
            onChange={(e) => {
              setUserId(e.target.value);
              setPage(1);
            }}
            placeholder="用户编号 / 手机号"
          />
        </div>
        <div className="w-[150px] space-y-1">
          <label className="text-xs text-muted-foreground">订单类型</label>
          <Select
            value={orderType || "__all__"}
            onValueChange={(v) => {
              setOrderType(!v || v === "__all__" ? "" : v);
              setPage(1);
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder="全部" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">全部</SelectItem>
              <SelectItem value="recharge">算力充值</SelectItem>
              <SelectItem value="subscription">会员开通</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="w-[130px] space-y-1">
          <label className="text-xs text-muted-foreground">状态</label>
          <Select
            value={status || "__all__"}
            onValueChange={(v) => {
              setStatus(!v || v === "__all__" ? "" : v);
              setPage(1);
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder="全部" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">全部</SelectItem>
              <SelectItem value="pending">待支付</SelectItem>
              <SelectItem value="completed">已完成</SelectItem>
              <SelectItem value="failed">失败</SelectItem>
              <SelectItem value="expired">已过期</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="w-[130px] space-y-1">
          <label className="text-xs text-muted-foreground">支付渠道</label>
          <Select
            value={paymentChannel || "__all__"}
            onValueChange={(v) => {
              setPaymentChannel(!v || v === "__all__" ? "" : v);
              setPage(1);
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder="全部" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">全部</SelectItem>
              <SelectItem value="alipay">支付宝</SelectItem>
              <SelectItem value="direct">直连演示</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="w-[220px] space-y-1">
          <label className="text-xs text-muted-foreground">商户订单号</label>
          <Input
            value={outTradeNo}
            onChange={(e) => {
              setOutTradeNo(e.target.value);
              setPage(1);
            }}
            placeholder="JM..."
          />
        </div>
        <div className="w-[150px] space-y-1">
          <label className="text-xs text-muted-foreground">开始日期</label>
          <Input
            type="date"
            value={dateFrom}
            onChange={(e) => {
              setDateFrom(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <div className="w-[150px] space-y-1">
          <label className="text-xs text-muted-foreground">结束日期</label>
          <Input
            type="date"
            value={dateTo}
            onChange={(e) => {
              setDateTo(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            setUserId("");
            setOrderType("");
            setStatus("");
            setPaymentChannel("");
            setOutTradeNo("");
            setDateFrom("");
            setDateTo("");
            setPage(1);
          }}
        >
          重置筛选
        </Button>
      </div>

      <div className="mb-3 flex items-center justify-between text-sm text-muted-foreground">
        <span>
          共 {data?.total ?? 0} 条
          {isFetching && !isLoading ? (
            <Loader2 className="ml-2 inline h-3.5 w-3.5 animate-spin" />
          ) : null}
        </span>
        <span>
          第 {page} / {totalPages} 页
        </span>
      </div>

      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-[1080px] text-sm">
          <thead className="border-b border-border bg-muted/40 text-left text-xs text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">创建时间</th>
              <th className="px-4 py-3 font-medium">用户</th>
              <th className="px-4 py-3 font-medium">类型</th>
              <th className="px-4 py-3 font-medium">支付金额</th>
              <th className="px-4 py-3 font-medium">算力/套餐</th>
              <th className="px-4 py-3 font-medium">渠道</th>
              <th className="px-4 py-3 font-medium">状态</th>
              <th className="px-4 py-3 font-medium">商户订单号</th>
                  <th className="px-4 py-3 font-medium">完成时间</th>
                  <th className="px-4 py-3 font-medium">支付过期时间</th>
                </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={10} className="px-4 py-10 text-center text-muted-foreground">
                  <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                </td>
              </tr>
            ) : (data?.items ?? []).length === 0 ? (
              <tr>
                <td colSpan={10} className="px-4 py-10 text-center text-muted-foreground">
                  暂无充值记录
                </td>
              </tr>
            ) : (
              (data?.items ?? []).map((order) => (
                <tr key={order.id} className="border-b border-border/60 hover:bg-muted/20">
                  <td className="px-4 py-3 whitespace-nowrap tabular-nums">
                    {formatDateTimeCN(order.createdAt)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="font-medium">{order.userDisplayName || "—"}</div>
                    <div className="text-xs text-muted-foreground">
                      {normalizeUserNo(order.userNo)}
                      {order.userPhone ? ` · ${order.userPhone}` : ""}
                    </div>
                  </td>
                  <td className="px-4 py-3">{order.orderTypeLabel}</td>
                  <td className="px-4 py-3 font-mono tabular-nums">
                    {formatFenToYuan(order.payAmountFen)}
                  </td>
                  <td className="px-4 py-3">
                    {order.orderType === "subscription" ? (
                      <span>{order.planName || "会员套餐"}</span>
                    ) : (
                      <span className="font-mono tabular-nums">{order.amount} 算力</span>
                    )}
                  </td>
                  <td className="px-4 py-3">{order.paymentChannelLabel}</td>
                  <td className="px-4 py-3">
                    <span
                      className={cn(
                        "inline-flex rounded-full px-2 py-0.5 text-xs font-medium",
                        STATUS_STYLES[order.status] ?? "bg-muted text-muted-foreground"
                      )}
                    >
                      {order.statusLabel}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{order.outTradeNo}</td>
                  <td className="px-4 py-3 whitespace-nowrap tabular-nums text-muted-foreground">
                    {order.completedAt ? formatDateTimeCN(order.completedAt) : "—"}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap tabular-nums text-muted-foreground">
                    {order.expiresLabel
                      ? order.expiresLabel
                      : order.expiresAt
                        ? formatDateTimeCN(order.expiresAt)
                        : "—"}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex justify-end gap-2">
        <Button
          type="button"
          variant="outline"
          disabled={page <= 1}
          onClick={() => setPage((p) => Math.max(1, p - 1))}
        >
          上一页
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={page >= totalPages}
          onClick={() => setPage((p) => p + 1)}
        >
          下一页
        </Button>
      </div>

      <p className="mt-6 text-xs text-muted-foreground">
        入账后的算力变动可在
        <Link href="/admin/credit-transactions" className="mx-1 text-primary hover:underline">
          算力变动记录
        </Link>
        中查看；单用户详情见
        <Link href="/admin/users" className="mx-1 text-primary hover:underline">
          用户管理
        </Link>
        。
      </p>
    </div>
  );
}
