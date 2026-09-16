"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { AdminHeader } from "@/components/admin/AdminShell";
import { useAdminAuth } from "@/components/admin/AdminAuthContext";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  getAdminPermissionCatalog,
  listAdminAccountsWithPermissions,
  putAdminAccountPermissions,
  type AdminAccountPermissions,
} from "@/lib/api/admin";
import { normalizeUserNo } from "@/lib/admin/displayIds";
import { cn } from "@/lib/utils";

/** 管理员功能权限分配页：超管 / 持有「管理员权限设置」的账号可给普通管理员勾选功能 */
export default function AdminPermissionsPage() {
  const queryClient = useQueryClient();
  const { permissions: myPermissions, isSuperAdmin } = useAdminAuth();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<string[]>([]);

  const catalogQuery = useQuery({
    queryKey: ["admin", "permission-catalog"],
    queryFn: getAdminPermissionCatalog,
  });

  const adminsQuery = useQuery({
    queryKey: ["admin", "permission-admins"],
    queryFn: listAdminAccountsWithPermissions,
  });

  const selected = useMemo(() => {
    const items = adminsQuery.data?.items ?? [];
    return items.find((a) => a.id === selectedId) ?? items[0] ?? null;
  }, [adminsQuery.data?.items, selectedId]);

  useEffect(() => {
    if (!selected) {
      setDraft([]);
      return;
    }
    setSelectedId(selected.id);
    setDraft(selected.isSuperAdmin ? selected.permissions : [...selected.permissions]);
  }, [selected]);

  const saveMutation = useMutation({
    mutationFn: () => putAdminAccountPermissions(selected!.id, draft),
    onSuccess: () => {
      toast.success("权限已保存");
      void queryClient.invalidateQueries({ queryKey: ["admin", "permission-admins"] });
    },
    onError: (err: Error) => toast.error(err.message || "保存失败"),
  });

  const groups = useMemo(() => {
    const items = catalogQuery.data?.items ?? [];
    const map = new Map<string, typeof items>();
    for (const item of items) {
      const list = map.get(item.group) ?? [];
      list.push(item);
      map.set(item.group, list);
    }
    return [...map.entries()];
  }, [catalogQuery.data?.items]);

  /** 非超管只能勾选自己已有的权限 */
  const canToggle = (key: string) => isSuperAdmin || myPermissions.includes(key);

  const toggle = (key: string) => {
    if (!canToggle(key)) {
      toast.error("不能授出当前账号没有的权限");
      return;
    }
    setDraft((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  };

  const selectAllGrantable = () => {
    const keys = (catalogQuery.data?.items ?? [])
      .map((i) => i.key)
      .filter((k) => canToggle(k));
    setDraft(keys);
  };

  const clearAll = () => setDraft([]);

  return (
    <div>
      <AdminHeader
        title="管理员权限"
        description="为普通管理员勾选可访问的后台功能；可授予「管理员权限设置」以便其继续向下授权。超级管理员默认拥有全部权限且不可改。"
      />

      <div className="grid gap-6 lg:grid-cols-[280px_1fr]">
        <section className="rounded-xl border border-border bg-card">
          <div className="border-b border-border px-4 py-3 text-sm font-medium">管理员列表</div>
          {adminsQuery.isLoading ? (
            <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              加载中…
            </div>
          ) : (
            <ul className="max-h-[70vh] overflow-y-auto p-2">
              {(adminsQuery.data?.items ?? []).map((admin) => (
                <AdminListRow
                  key={admin.id}
                  admin={admin}
                  active={selected?.id === admin.id}
                  onClick={() => setSelectedId(admin.id)}
                />
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-xl border border-border bg-card p-5">
          {!selected ? (
            <p className="text-sm text-muted-foreground">暂无管理员账号</p>
          ) : (
            <>
              <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold">{selected.displayName || "未命名"}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {selected.phone || "无手机号"} · {normalizeUserNo(selected.userNo) || selected.id}
                  </p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {selected.isSuperAdmin ? (
                    <Badge>超级管理员</Badge>
                  ) : (
                    <>
                      <Button type="button" variant="outline" size="sm" onClick={selectAllGrantable}>
                        全选可授出
                      </Button>
                      <Button type="button" variant="outline" size="sm" onClick={clearAll}>
                        清空
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        disabled={saveMutation.isPending}
                        onClick={() => saveMutation.mutate()}
                      >
                        {saveMutation.isPending ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          "保存权限"
                        )}
                      </Button>
                    </>
                  )}
                </div>
              </div>

              {selected.isSuperAdmin ? (
                <p className="rounded-lg bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
                  超级管理员固定拥有全部后台功能权限，无需也不允许通过此处修改。
                </p>
              ) : (
                <div className="space-y-6">
                  {groups.map(([group, items]) => (
                    <div key={group}>
                      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        {group}
                      </p>
                      <div className="grid gap-2 sm:grid-cols-2">
                        {items.map((item) => {
                          const checked = draft.includes(item.key);
                          const disabled = !canToggle(item.key);
                          return (
                            <label
                              key={item.key}
                              className={cn(
                                "flex cursor-pointer items-start gap-3 rounded-lg border border-border px-3 py-2.5 text-sm transition-colors",
                                checked ? "border-primary/40 bg-primary/5" : "hover:bg-muted/40",
                                disabled && "cursor-not-allowed opacity-50"
                              )}
                            >
                              <input
                                type="checkbox"
                                className="mt-0.5"
                                checked={checked}
                                disabled={disabled}
                                onChange={() => toggle(item.key)}
                              />
                              <span>
                                <span className="font-medium">{item.label}</span>
                                <span className="mt-0.5 block text-xs text-muted-foreground">
                                  {item.key}
                                </span>
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

function AdminListRow({
  admin,
  active,
  onClick,
}: {
  admin: AdminAccountPermissions;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full flex-col rounded-lg px-3 py-2.5 text-left text-sm transition-colors",
        active ? "bg-primary/15 text-primary" : "hover:bg-muted"
      )}
    >
      <span className="flex items-center gap-2 font-medium">
        {admin.displayName || "未命名"}
        {admin.isSuperAdmin ? (
          <Badge variant="secondary" className="text-[10px]">
            超管
          </Badge>
        ) : null}
      </span>
      <span className="mt-0.5 text-xs text-muted-foreground">{admin.phone || "—"}</span>
    </button>
  );
}
