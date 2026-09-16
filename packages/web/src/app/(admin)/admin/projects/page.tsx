"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AdminHeader } from "@/components/admin/AdminShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { getAdminProjectDetail, listAdminProjects } from "@/lib/api/admin";
import { usePersistedAdminQuery } from "@/lib/admin/usePersistedAdminQuery";
import { formatDateTimeCN } from "@/lib/formatDateTime";
import type { AdminProject, AdminProjectDetail } from "@/types/admin";

const PROJECTS_QUERY_DEFAULTS = {
  searchInput: "",
  search: "",
  page: 1,
};

function ProjectDetailDialog({
  project,
  open,
  onOpenChange,
}: {
  project: AdminProject | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["admin", "project", project?.id],
    queryFn: () => getAdminProjectDetail(project!.id),
    enabled: open && Boolean(project?.id),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{project?.title ?? "项目详情"}</DialogTitle>
          <DialogDescription>
            {project?.projectNo} · {project?.ownerDisplayName}
            {project?.ownerPhone ? ` · ${project.ownerPhone}` : ""}
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <p className="text-sm text-muted-foreground">加载详情…</p>
        ) : isError || !data ? (
          <p className="text-sm text-destructive">加载项目详情失败</p>
        ) : (
          <ProjectDetailBody detail={data} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function ProjectDetailBody({ detail }: { detail: AdminProjectDetail }) {
  return (
    <div className="space-y-6">
      <section className="grid gap-3 sm:grid-cols-2">
        <InfoItem label="项目编号" value={detail.projectNo || "—"} mono />
        <InfoItem label="内部项目 ID" value={detail.id} mono muted />
        <InfoItem label="所属用户" value={detail.ownerDisplayName} />
        <InfoItem label="手机号" value={detail.ownerPhone || "—"} />
        <InfoItem label="创建时间" value={formatDateTimeCN(detail.createdAt)} />
        <InfoItem label="最后修改" value={formatDateTimeCN(detail.updatedAt)} />
        <InfoItem label="工作流数" value={String(detail.workflowCount)} />
      </section>

      <section>
        <h3 className="mb-2 text-sm font-medium">资产数量明细</h3>
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">图片 {detail.assetStats.image}</Badge>
          <Badge variant="secondary">视频 {detail.assetStats.video}</Badge>
          <Badge variant="secondary">音频 {detail.assetStats.audio}</Badge>
          <Badge variant="outline">合计 {detail.assetStats.total}</Badge>
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-medium">
          节点列表
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            来自最新工作流{detail.latestWorkflowId ? ` (${detail.latestWorkflowId.slice(0, 8)}…)` : ""}
          </span>
        </h3>
        {detail.nodes.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
            暂无节点数据
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-border bg-muted/40 text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">节点名称</th>
                  <th className="px-3 py-2 font-medium">类型</th>
                  <th className="px-3 py-2 font-medium">节点 ID</th>
                </tr>
              </thead>
              <tbody>
                {detail.nodes.map((node) => (
                  <tr key={node.id} className="border-b border-border/60">
                    <td className="px-3 py-2">{node.label}</td>
                    <td className="px-3 py-2">
                      <Badge variant="outline">{node.type}</Badge>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-muted-foreground">{node.id}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function InfoItem({
  label,
  value,
  mono,
  muted,
}: {
  label: string;
  value: string;
  mono?: boolean;
  muted?: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-muted/20 px-3 py-2">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={`mt-0.5 text-sm ${mono ? "font-mono text-xs break-all" : ""} ${muted ? "text-muted-foreground" : ""}`}
      >
        {value}
      </p>
    </div>
  );
}

export default function AdminProjectsPage() {
  const searchParams = useSearchParams();
  // 筛选条件写入 sessionStorage，切换选项卡后再回来时保留
  const [query, setQuery] = usePersistedAdminQuery(
    "canvas-admin-projects-query-v1",
    PROJECTS_QUERY_DEFAULTS
  );
  const { searchInput, search, page } = query;
  const [selected, setSelected] = useState<AdminProject | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const pageSize = 20;

  useEffect(() => {
    const fromUrl = searchParams.get("search")?.trim();
    if (fromUrl) {
      setQuery({ searchInput: fromUrl, search: fromUrl, page: 1 });
    }
  }, [searchParams, setQuery]);

  useEffect(() => {
    const timer = setTimeout(() => {
      const next = searchInput.trim();
      setQuery((prev) => {
        if (prev.search === next) return prev;
        return { ...prev, search: next, page: 1 };
      });
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput, setQuery]);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["admin", "projects", search, page],
    queryFn: () => listAdminProjects({ search: search || undefined, page, pageSize }),
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;

  const openDetail = (project: AdminProject) => {
    setSelected(project);
    setDetailOpen(true);
  };

  return (
    <>
      <AdminHeader
        title="项目列表"
        description="全站所有用户的画布项目，支持按项目名、项目 ID、用户名、手机号模糊筛选"
      />

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="min-w-[280px] flex-1 space-y-1">
          <label className="text-xs text-muted-foreground">项目筛选</label>
          <Input
            placeholder="项目名 / 项目 ID / 用户名 / 手机号"
            value={searchInput}
            onChange={(e) => setQuery({ searchInput: e.target.value })}
          />
        </div>
        <Button variant="outline" onClick={() => refetch()} disabled={isFetching}>
          刷新
        </Button>
      </div>

      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full min-w-[960px] text-left text-sm">
          <thead className="border-b border-border bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">项目名</th>
              <th className="px-4 py-3 font-medium">项目 ID</th>
              <th className="px-4 py-3 font-medium">所属用户</th>
              <th className="px-4 py-3 font-medium">手机号</th>
              <th className="px-4 py-3 font-medium">创建时间</th>
              <th className="px-4 py-3 font-medium">最后修改</th>
              <th className="px-4 py-3 font-medium">详情</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-muted-foreground">
                  加载中…
                </td>
              </tr>
            ) : isError ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-destructive">
                  加载项目列表失败
                </td>
              </tr>
            ) : !data?.items.length ? (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-muted-foreground">
                  暂无项目
                </td>
              </tr>
            ) : (
              data.items.map((project) => (
                <tr key={project.id} className="border-b border-border/60 hover:bg-muted/20">
                  <td className="px-4 py-3 font-medium">{project.title}</td>
                  <td className="px-4 py-3">
                    <div className="font-mono text-xs">{project.projectNo || `${project.id.slice(0, 8)}…`}</div>
                  </td>
                  <td className="px-4 py-3">{project.ownerDisplayName}</td>
                  <td className="px-4 py-3 tabular-nums">{project.ownerPhone || "—"}</td>
                  <td className="px-4 py-3 whitespace-nowrap">{formatDateTimeCN(project.createdAt)}</td>
                  <td className="px-4 py-3 whitespace-nowrap">{formatDateTimeCN(project.updatedAt)}</td>
                  <td className="px-4 py-3">
                    <Button variant="outline" size="sm" onClick={() => openDetail(project)}>
                      详情
                    </Button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {data ? (
        <div className="mt-4 flex items-center justify-between text-sm text-muted-foreground">
          <span>
            共 {data.total} 条 · 第 {data.page} / {totalPages} 页
          </span>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => setQuery({ page: Math.max(1, page - 1) })}
            >
              上一页
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= totalPages}
              onClick={() => setQuery({ page: page + 1 })}
            >
              下一页
            </Button>
          </div>
        </div>
      ) : null}

      <ProjectDetailDialog
        project={selected}
        open={detailOpen}
        onOpenChange={setDetailOpen}
      />
    </>
  );
}
