"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowRight,
  ArrowUp,
  Award,
  BadgeCheck,
  Boxes,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  Folder,
  Heart,
  ImageUp,
  MoreHorizontal,
  Palette,
  Pencil,
  Plus,
  Search,
  Smile,
  Sparkles,
  Star,
  Trash2,
  Users,
  Zap,
} from "lucide-react";
import * as api from "@/lib/api/projects";
import { listCreditActivities } from "@/lib/api/credits";
import { listSkills, type SkillItem } from "@/lib/api/skills";
import { getSiteActivities, getSiteDiscover, getSiteFooter, isOngoingSiteActivity } from "@/lib/api/site";
import {
  GALLERY_COVER_OSS_PROCESS,
  resolveProjectCoverDisplayUrl,
  withOssImageProcess,
} from "@/lib/api/storageUrl";
import {
  deleteWorkflowPublication,
  listWorkflowPublications,
  type PublicationScope,
  type PublicationSort,
} from "@/lib/api/workflowPublications";
import { ApiError } from "@/lib/api/client";
import { HuabuPublicShell } from "@/components/huabu/HuabuPublicShell";
import { PublishWorkflowModal } from "@/components/huabu/PublishWorkflowModal";
import { WorkflowPublicationDetailModal } from "@/components/huabu/WorkflowPublicationDetailModal";
import { GalleryThumb } from "@/components/huabu/GalleryThumb";
import { DiscoverCornerPopupCard } from "@/components/huabu/DiscoverCornerPopup";
import { HomeActivityCarousel } from "@/components/huabu/HomeActivityCarousel";
import { ViralRemakeDialog } from "@/components/huabu/ViralRemakeDialog";
import {
  CreationPromptBox,
  focusCreationPromptBox,
} from "@/components/huabu/CreationPromptBox";
import { useAuthStore } from "@/stores/authStore";
import { VIRAL_REMAKE_SKILL } from "@/lib/canvas/viralRemakeSession";
import { OVERSEAS_LOCALIZE_SKILL } from "@/lib/canvas/overseasSession";
import {
  GALLERY_PAGE_SIZE,
  galleryFilters as fallbackGalleryFilters,
  highlightFeatures as fallbackHighlights,
} from "@/components/huabu/huabuData";
import { localMarketingCover } from "@/lib/huabu/localMarketingCover";
import type { Project } from "@/types";

/** 首页 Hero 提示芯片：优先展示公开 Skill 目录 */
const HERO_SKILL_CHIP_LIMIT = 6;

const FAVORITES_STORAGE_PREFIX = "jm_canvas_project_favorites:";

/** 算力活动无封面时的默认图 */
const ACTIVITY_COVERS = [
  localMarketingCover(0),
  localMarketingCover(1),
  localMarketingCover(2),
];

function pickHeroVideo(urls: string[], exclude?: string) {
  const pool = exclude ? urls.filter((u) => u !== exclude) : urls;
  if (!pool.length) return "";
  return pool[Math.floor(Math.random() * pool.length)] ?? "";
}

function loadFavoriteIds(userId: string | undefined): string[] {
  if (!userId || typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(`${FAVORITES_STORAGE_PREFIX}${userId}`);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function saveFavoriteIds(userId: string, ids: string[]) {
  localStorage.setItem(`${FAVORITES_STORAGE_PREFIX}${userId}`, JSON.stringify(ids));
}

/** 相对时间（东八区语义），用于项目卡片展示 */
function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diff = Date.now() - then;
  const min = Math.floor(diff / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day} 天前`;
  return new Date(then).toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai" });
}

/** huabu 发现首页：公共壳 + 真实最近项目与创建入口 */
export default function DiscoverPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const userId = user?.id;

  const [projectTab, setProjectTab] = useState<"mine" | "collaboration">("mine");
  const [favoriteIds, setFavoriteIds] = useState<string[]>([]);
  const [projectMenu, setProjectMenu] = useState<string | null>(null);
  const [workflowTab, setWorkflowTab] = useState<"templates" | "published" | "purchased">("templates");
  const [activeFilter, setActiveFilter] = useState("全部");
  /** 作品广场排序：最新 / 最热（按点赞） */
  const [gallerySort, setGallerySort] = useState<PublicationSort>("latest");
  const [galleryPage, setGalleryPage] = useState(1);
  const [publishOpen, setPublishOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  /** 爆款复刻上传弹窗 */
  const [viralRemakeOpen, setViralRemakeOpen] = useState(false);
  /** 一键出海上传弹窗 */
  const coverInputRef = useRef<HTMLInputElement>(null);
  const coverTargetRef = useRef<string | null>(null);

  useEffect(() => {
    setFavoriteIds(loadFavoriteIds(userId));
  }, [userId]);

  const { data: ownedProjects = [] } = useQuery({
    queryKey: ["projects", userId],
    queryFn: api.listProjects,
    enabled: Boolean(userId),
  });
  const { data: sharedProjects = [] } = useQuery({
    queryKey: ["projects", userId, "shared"],
    queryFn: api.listSharedProjects,
    enabled: Boolean(userId),
  });

  // 发现页运营配置（后台可改；失败时回退静态默认）
  const { data: discoverConfig } = useQuery({
    queryKey: ["site", "discover"],
    queryFn: getSiteDiscover,
  });

  // 公开 Skill 目录（Hero 提示芯片真实数据源）
  const { data: skillsCatalog } = useQuery({
    queryKey: ["skills", "catalog"],
    queryFn: () => listSkills(),
    staleTime: 60_000,
  });

  // 首页 Footer：关于我们跳转 / 联系我们二维码 / 社交链接
  const { data: footerConfig } = useQuery({
    queryKey: ["site", "footer"],
    queryFn: getSiteFooter,
  });
  const [contactQrOpen, setContactQrOpen] = useState(false);

  // 活动预览：登录态走可领取列表；游客走公开活动展示
  const { data: creditActivitiesData } = useQuery({
    queryKey: ["credits", "activities"],
    queryFn: listCreditActivities,
    enabled: Boolean(userId),
  });
  const { data: siteActivitiesData } = useQuery({
    queryKey: ["site", "activities"],
    queryFn: getSiteActivities,
    enabled: !userId,
  });

  const heroVideos = useMemo(
    () => (discoverConfig?.heroVideos ?? []).map((v) => v.url).filter(Boolean),
    [discoverConfig?.heroVideos]
  );
  const [heroBg, setHeroBg] = useState("");
  useEffect(() => {
    if (heroVideos.length) setHeroBg(pickHeroVideo(heroVideos));
  }, [heroVideos]);

  /** Hero 芯片：按 sortOrder 取公开 Skill；目录为空时不展示假数据 */
  const heroSkillChips = useMemo(() => {
    const items = [...(skillsCatalog?.items ?? [])].sort(
      (a, b) => (a.sortOrder ?? 0) - (b.sortOrder ?? 0)
    );
    return items.slice(0, HERO_SKILL_CHIP_LIMIT);
  }, [skillsCatalog?.items]);

  const highlightFeatures = discoverConfig?.highlightFeatures?.length
    ? discoverConfig.highlightFeatures
    : fallbackHighlights;
  const storyFeature = discoverConfig?.storyFeature ?? {
    label: "剧情故事创作",
    imageUrl: localMarketingCover(0),
    href: "/skills",
  };
  const skillMachine = discoverConfig?.skillMachine ?? {
    label: "Skill 技能制造机",
    href: "/skills",
    imageUrl: "",
  };
  const heroTitle = discoverConfig?.heroTitle ?? "想象";
  const heroEm = discoverConfig?.heroEm ?? "正在发生的画面";
  const creationPlaceholder =
    discoverConfig?.creationPlaceholder ??
    "拖拽 / 粘贴图片到这里，试试智能体技能、视觉风格、素材库";

  const homeActivities = useMemo(() => {
    // 游客公开接口含往期活动；首页只展示进行中，避免已结束/下架活动仍出现在未登录首页。
    const rawItems = userId
      ? (creditActivitiesData?.items ?? [])
      : (siteActivitiesData?.items ?? []);
    const items = rawItems.filter(isOngoingSiteActivity);
    // 轮播可展示多条；封面优先用后台 coverUrl
    return items.slice(0, 8).map((activity, index) => ({
      id: activity.id,
      title: activity.title,
      subtitle: activity.description || `领取 ${activity.amount} 算力`,
      image:
        ("coverUrl" in activity && activity.coverUrl) ||
        ACTIVITY_COVERS[index % ACTIVITY_COVERS.length],
    }));
  }, [creditActivitiesData, siteActivitiesData, userId]);

  const createMutation = useMutation({
    mutationFn: () => api.createProject("未命名项目"),
    onSuccess: (project: Project) => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
      router.push(`/${project.id}`);
    },
    onError: () => toast.error("新建项目失败，请稍后重试"),
  });

  const beginCreate = () => {
    if (!user) {
      router.push("/login?next=%2F");
      return;
    }
    createMutation.mutate();
  };

  /** 爆款复刻 / 爆款拉片复刻：打开上传弹窗（需登录） */
  const openViralRemake = () => {
    if (!user) {
      router.push("/login?next=%2F");
      return;
    }
    setViralRemakeOpen(true);
  };

  /** 一键出海：预选技能到创作框（可加附件/说明后发送进 Agent，不再强制弹窗） */
  const preselectOverseasLocalize = (title = "一键出海") => {
    if (!user) {
      router.push("/login?next=%2F");
      return;
    }
    try {
      sessionStorage.setItem("jumeng:preselect_skill", OVERSEAS_LOCALIZE_SKILL);
    } catch {
      /* ignore */
    }
    focusCreationPromptBox();
    toast.message(`已选择「${title}」，可添加参考视频与补充说明后发送`);
  };

  /** 点击 Hero Skill 芯片：与 /skills 页同语义启动 */
  const openHeroSkill = async (skill: SkillItem) => {
    if (!user) {
      router.push("/login?next=%2F");
      return;
    }
    const kind = skill.entryKind || skill.slug;
    const isViral = kind === "viral_remake" || kind === VIRAL_REMAKE_SKILL;
    const isOverseas =
      kind === "overseas_localize" ||
      kind === "overseas" ||
      kind === OVERSEAS_LOCALIZE_SKILL;
    const isCinematic =
      kind === "product_cinematic_commercial" ||
      skill.slug === "product_cinematic_commercial";
    // 爆款复刻 / 一键出海：预选到创作框，不直进项目
    if (isViral || isOverseas) {
      try {
        sessionStorage.setItem(
          "jumeng:preselect_skill",
          skill.slug || (isViral ? VIRAL_REMAKE_SKILL : OVERSEAS_LOCALIZE_SKILL)
        );
      } catch {
        /* ignore */
      }
      focusCreationPromptBox();
      toast.message(
        isViral
          ? `已选择「${skill.title}」，可上传参考视频与补充说明后发送`
          : `已选择「${skill.title}」，可添加参考视频与补充说明后发送`
      );
      return;
    }
    try {
      sessionStorage.setItem("jumeng:preselect_skill", skill.slug);
    } catch {
      /* ignore */
    }
    focusCreationPromptBox();
    toast.message(
      isCinematic
        ? `已选择「${skill.title}」，请上传产品参考图并补充卖点后发送`
        : `已选择「${skill.title}」，输入想法后发送即可`
    );
  };

  const isViralRemakeEntry = (label: string, href?: string) => {
    const text = (label || "").trim();
    if (text.includes("爆款复刻") || text.includes("爆款拉片")) return true;
    const h = (href || "").trim();
    return h === "#viral-remake" || h.includes("viral-remake");
  };

  const isOverseasEntry = (label: string, href?: string) => {
    const text = (label || "").trim();
    if (text.includes("一键出海") || text.includes("出海本地化")) return true;
    const h = (href || "").trim();
    return h === "#overseas-localize" || h.includes("overseas-localize") || h.includes("overseas");
  };

  const recentProjects = (projectTab === "mine" ? ownedProjects : sharedProjects).slice(0, 3);

  const toggleFavorite = (id: string) => {
    if (!userId) return;
    setFavoriteIds((cur) => {
      const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
      saveFavoriteIds(userId, next);
      return next;
    });
  };

  const handleRename = async (project: Project) => {
    setProjectMenu(null);
    const title = window.prompt("重命名项目", project.title);
    if (!title || title === project.title) return;
    try {
      await api.updateProject(project.id, { title });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    } catch {
      toast.error("重命名失败");
    }
  };

  const handleDelete = async (project: Project) => {
    setProjectMenu(null);
    if (!window.confirm(`确认删除项目「${project.title}」？`)) return;
    try {
      await api.deleteProject(project.id);
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    } catch {
      toast.error("删除失败");
    }
  };

  const handleCoverPick = (projectId: string) => {
    setProjectMenu(null);
    coverTargetRef.current = projectId;
    coverInputRef.current?.click();
  };

  const onCoverSelected = async (file: File | undefined) => {
    const projectId = coverTargetRef.current;
    if (!projectId || !file) return;
    try {
      await api.uploadProjectCover(projectId, file);
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    } catch {
      toast.error("上传封面失败");
    }
  };

  const publicationScope: PublicationScope =
    workflowTab === "published" ? "mine" : workflowTab === "purchased" ? "used" : "public";

  const {
    data: publicationList,
    isLoading: publicationsLoading,
    isError: publicationsError,
  } = useQuery({
    queryKey: [
      "workflow-publications",
      publicationScope,
      activeFilter,
      gallerySort,
      galleryPage,
      userId,
    ],
    queryFn: () =>
      listWorkflowPublications({
        scope: publicationScope,
        category: activeFilter,
        sort: gallerySort,
        page: galleryPage,
        pageSize: GALLERY_PAGE_SIZE,
      }),
    enabled: publicationScope === "public" || Boolean(userId),
  });

  const galleryItems = publicationList?.items ?? [];
  const galleryTotal = publicationList?.total ?? 0;
  // 分类 Tab 权威：后台发现页 galleryFilters；其次发布列表返回；最后静态兜底
  const galleryFilters = useMemo(() => {
    const fromConfig = discoverConfig?.galleryFilters?.filter(Boolean) ?? [];
    if (fromConfig.length > 0) {
      return fromConfig.includes("全部") ? fromConfig : ["全部", ...fromConfig];
    }
    if (publicationList?.categories?.length) {
      return ["全部", ...publicationList.categories];
    }
    return fallbackGalleryFilters;
  }, [discoverConfig?.galleryFilters, publicationList?.categories]);

  // 后台改分类后，当前 Tab 若不在列表中则回到「全部」
  useEffect(() => {
    if (galleryFilters.length > 0 && !galleryFilters.includes(activeFilter)) {
      setActiveFilter("全部");
      setGalleryPage(1);
    }
  }, [galleryFilters, activeFilter]);

  const galleryTotalPages = Math.max(1, Math.ceil(galleryTotal / GALLERY_PAGE_SIZE));
  const changeGalleryPage = (page: number) => {
    setGalleryPage(Math.min(galleryTotalPages, Math.max(1, page)));
    document.getElementById("作品广场")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // 「我的发布」删除：仅作者本人，确认后硬删并可重新发布同项目
  const deletePublicationMutation = useMutation({
    mutationFn: (id: string) => deleteWorkflowPublication(id),
    onSuccess: (_data, id) => {
      toast.success("已删除发布");
      void queryClient.invalidateQueries({ queryKey: ["workflow-publications"] });
      setDetailId((cur) => (cur === id ? null : cur));
    },
    onError: (err: unknown) => {
      toast.error(
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "删除失败"
      );
    },
  });

  const handleDeletePublication = (itemId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (deletePublicationMutation.isPending) return;
    if (!window.confirm("确定删除该发布？删除后可重新发布同一项目。")) return;
    deletePublicationMutation.mutate(itemId);
  };

  const patchPublicationLike = (id: string, liked: boolean, likeCount: number) => {
    void queryClient.setQueryData(
      ["workflow-publications", publicationScope, activeFilter, gallerySort, galleryPage, userId],
      (prev: typeof publicationList) => {
        if (!prev) return prev;
        return {
          ...prev,
          items: prev.items.map((it) =>
            it.id === id ? { ...it, liked, likeCount } : it
          ),
        };
      }
    );
    // 最热排序下点赞后重拉列表，保证顺序与计数一致
    if (gallerySort === "hot") {
      void queryClient.invalidateQueries({ queryKey: ["workflow-publications"] });
    }
  };

  return (
    <HuabuPublicShell>
      <DiscoverCornerPopupCard
        config={discoverConfig?.cornerPopup}
        loggedIn={Boolean(user)}
      />
      <input
        ref={coverInputRef}
        type="file"
        accept="image/*"
        className="project-cover-input"
        onChange={(e) => {
          void onCoverSelected(e.target.files?.[0]);
          e.target.value = "";
        }}
      />

      <section className="hero-stage">
        {heroBg ? (
          <video
            key={heroBg}
            className="hero-video"
            src={heroBg}
            autoPlay
            muted
            loop
            playsInline
            preload="auto"
            onError={() => setHeroBg((current) => pickHeroVideo(heroVideos, current))}
            onLoadedData={(e) => {
              e.currentTarget.play().catch(() => {});
            }}
            aria-hidden="true"
          />
        ) : null}
        <div className="hero-video-mask" aria-hidden="true" />
        <div className="orb orb-one" />
        <div className="orb orb-two" />
        <div className="hero-grid" aria-hidden="true" />

        <div className="hero">
          <h1 className="reveal-two">
            {heroTitle} <em>{heroEm}</em>
          </h1>

          <div className="reveal-four mx-auto flex w-full max-w-[860px] justify-center">
            <CreationPromptBox
              loginNext="/"
              placeholder={creationPlaceholder}
              onOpenViralRemake={openViralRemake}
            />
          </div>

          <div className="prompt-suggestions reveal-four">
            {heroSkillChips.map((skill) => (
              <button
                type="button"
                className="prompt-chip"
                key={skill.id || skill.slug}
                onClick={() => void openHeroSkill(skill)}
              >
                {skill.coverUrl ? (
                  <img className="prompt-chip-thumb" src={skill.coverUrl} alt="" />
                ) : (
                  <span className="prompt-chip-thumb prompt-chip-thumb-fallback" aria-hidden="true">
                    <Sparkles size={14} strokeWidth={1.8} />
                  </span>
                )}
                <span>{skill.title}</span>
              </button>
            ))}
            <button type="button" className="prompt-chip prompt-chip-all" onClick={() => router.push("/skills")}>
              <span>全部Skill</span>
              <ArrowRight size={12} strokeWidth={2.2} />
            </button>
          </div>
        </div>
      </section>

      <section className="dashboard-section reveal-four">
        {user ? (
          <div className="dash-block">
          <div className="dash-head">
            <div className="project-tabs" role="tablist" aria-label="项目分类">
              <button
                type="button"
                className={projectTab === "mine" ? "active" : ""}
                role="tab"
                aria-selected={projectTab === "mine"}
                onClick={() => setProjectTab("mine")}
              >
                <Folder size={15} strokeWidth={1.8} />
                我的项目
              </button>
              <button
                type="button"
                className={projectTab === "collaboration" ? "active" : ""}
                role="tab"
                aria-selected={projectTab === "collaboration"}
                onClick={() => setProjectTab("collaboration")}
              >
                <Users size={15} strokeWidth={1.8} />
                与我协作
              </button>
            </div>
            <div className="dash-head-actions">
              <button type="button" className="dash-link" onClick={() => router.push("/projects")}>
                全部
                <ArrowRight size={12} strokeWidth={2.2} />
              </button>
            </div>
          </div>
          <div className="project-row">
            <button
              type="button"
              className="project-card project-new"
              onClick={beginCreate}
              disabled={createMutation.isPending}
            >
              <span className="project-new-icon">
                <Plus size={22} strokeWidth={1.8} />
              </span>
              <span>新建项目</span>
            </button>
            {recentProjects.map((project) => {
              const isFav = favoriteIds.includes(project.id);
              return (
                <div
                  className="project-card"
                  key={project.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => router.push(`/${project.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") router.push(`/${project.id}`);
                  }}
                >
                  <div className="project-actions" onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      className={`project-action${isFav ? " is-favorite" : ""}`}
                      aria-label={isFav ? "取消收藏" : "收藏项目"}
                      title={isFav ? "取消收藏" : "收藏"}
                      onClick={() => toggleFavorite(project.id)}
                    >
                      <Star size={15} strokeWidth={1.8} fill={isFav ? "currentColor" : "none"} />
                    </button>
                    <button
                      type="button"
                      className="project-action"
                      aria-label="更多项目操作"
                      title="更多"
                      aria-expanded={projectMenu === project.id}
                      onClick={() => setProjectMenu((cur) => (cur === project.id ? null : project.id))}
                    >
                      <MoreHorizontal size={17} strokeWidth={2} />
                    </button>
                  </div>
                  {projectMenu === project.id && (
                    <div className="project-menu" onClick={(e) => e.stopPropagation()}>
                      <button type="button" onClick={() => handleRename(project)}>
                        <Pencil size={14} strokeWidth={1.8} />
                        重命名
                      </button>
                      <button type="button" onClick={() => handleCoverPick(project.id)}>
                        <ImageUp size={14} strokeWidth={1.8} />
                        上传封面
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setProjectMenu(null);
                          toast.message("复制项目即将上线");
                        }}
                      >
                        <Copy size={14} strokeWidth={1.8} />
                        复制项目
                      </button>
                      <button type="button" className="project-menu-danger" onClick={() => handleDelete(project)}>
                        <Trash2 size={14} strokeWidth={1.8} />
                        删除项目
                      </button>
                    </div>
                  )}
                  <span className="project-cover" aria-hidden="true">
                    {(() => {
                      const coverSrc = resolveProjectCoverDisplayUrl(project.coverUrl);
                      return coverSrc ? (
                        <img src={coverSrc} alt="" />
                      ) : (
                        <Folder size={39} strokeWidth={1.5} />
                      );
                    })()}
                  </span>
                  <span className="project-info">
                    <span className="project-heading">
                      <span className="project-id">{project.projectNo}</span>
                      <strong className="project-title">{project.title}</strong>
                    </span>
                    <span className="project-details">
                      <span>{project.workflowCount ?? 1}个工作流</span>
                      <span className="project-time">{formatRelative(project.updatedAt)}</span>
                    </span>
                  </span>
                </div>
              );
            })}
          </div>
          </div>
        ) : null}

        <div className="dash-block">
          <div className="dash-head">
            <span className="dash-title">
              <Sparkles size={15} strokeWidth={1.8} />
              技能亮点
            </span>
            <button type="button" className="dash-link" onClick={() => router.push("/skills")}>
              全部
              <ArrowRight size={12} strokeWidth={2.2} />
            </button>
          </div>
          <div className="feature-board">
            <button
              type="button"
              className="feature-card feature-story"
              onClick={() => router.push(storyFeature.href || "/skills")}
            >
              {storyFeature.imageUrl ? (
                <img className="feature-cover" src={storyFeature.imageUrl} alt="" />
              ) : null}
              <span className="feature-label">
                {storyFeature.label} <ArrowRight size={13} strokeWidth={2.2} />
              </span>
            </button>
            <div className="feature-mini-grid">
              {highlightFeatures.map((item) => {
                const subtitle =
                  typeof item === "object" && "subtitle" in item && item.subtitle
                    ? String(item.subtitle).trim()
                    : "";
                return (
                <button
                  type="button"
                  className={`feature-card feature-mini feature-${"tone" in item ? item.tone : "purple"}`}
                  key={item.label}
                  onClick={() => {
                    const href =
                      typeof item === "object" && "href" in item && item.href ? String(item.href) : "/skills";
                    if (isViralRemakeEntry(item.label, href)) {
                      openViralRemake();
                      return;
                    }
                    if (isOverseasEntry(item.label, href)) {
                      preselectOverseasLocalize(item.label);
                      return;
                    }
                    router.push(href);
                  }}
                >
                  {"isNew" in item && item.isNew ? <span className="feature-new">New</span> : null}
                  <span className="feature-mini-copy">
                    <span className="feature-label">
                      {item.label} <ArrowRight size={12} strokeWidth={2.2} />
                    </span>
                    {subtitle ? <span className="feature-sub">{subtitle}</span> : null}
                  </span>
                </button>
                );
              })}
            </div>
            <button
              type="button"
              className="feature-card feature-skill"
              onClick={() => router.push(skillMachine.href || "/skills")}
            >
              {skillMachine.imageUrl ? (
                <img className="feature-cover" src={skillMachine.imageUrl} alt="" />
              ) : (
                <div className="feature-skill-art" aria-hidden="true">
                  <Zap size={28} strokeWidth={1.8} />
                </div>
              )}
              <span className="feature-label">
                {skillMachine.label} <ArrowRight size={13} strokeWidth={2.2} />
              </span>
            </button>
          </div>
        </div>

        {homeActivities.length > 0 ? (
          <div className="dash-block">
            <div className="dash-head">
              <span className="dash-title">
                <Award size={15} strokeWidth={1.8} />
                活动
              </span>
              <button type="button" className="dash-link" onClick={() => router.push("/activities")}>
                全部
                <ArrowRight size={12} strokeWidth={2.2} />
              </button>
            </div>
            <div className="activity-row">
              <HomeActivityCarousel
                items={homeActivities}
                onOpen={() => router.push("/activities")}
              />
            </div>
          </div>
        ) : null}
      </section>

      <section className="section gallery-section" id="作品广场">
        <div className="gallery-header">
          <div className="workflow-tabs" role="tablist" aria-label="工作流分类">
            {[
              { id: "templates", label: "全部工作流" },
              { id: "published", label: "我的发布" },
              { id: "purchased", label: "我的使用" },
            ].map((tab) => (
              <button
                type="button"
                key={tab.id}
                className={workflowTab === tab.id ? "active" : ""}
                role="tab"
                aria-selected={workflowTab === tab.id}
                onClick={() => {
                  setWorkflowTab(tab.id as typeof workflowTab);
                  setGalleryPage(1);
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <label className="gallery-search">
            <input type="search" placeholder="请输入搜索内容" aria-label="搜索作品" />
            <Search size={16} strokeWidth={1.8} />
          </label>
        </div>

        <div className="gallery-toolbar">
          <div className="gallery-filters" role="tablist" aria-label="作品分类">
            {galleryFilters.map((item) => (
              <button
                type="button"
                className={activeFilter === item ? "active" : ""}
                onClick={() => {
                  setActiveFilter(item);
                  setGalleryPage(1);
                }}
                role="tab"
                aria-selected={activeFilter === item}
                key={item}
              >
                {item}
              </button>
            ))}
          </div>
          <div className="gallery-sort" role="tablist" aria-label="排序方式">
            {(
              [
                { id: "latest" as const, label: "最新" },
                { id: "hot" as const, label: "最热" },
              ] as const
            ).map((opt) => (
              <button
                type="button"
                key={opt.id}
                className={gallerySort === opt.id ? "active" : ""}
                role="tab"
                aria-selected={gallerySort === opt.id}
                onClick={() => {
                  setGallerySort(opt.id);
                  setGalleryPage(1);
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        <div className="gallery-grid">
          {workflowTab === "published" && (
            <button
              type="button"
              className="gallery-publish-card"
              onClick={() => {
                if (!userId) {
                  toast.message("请先登录后再发布");
                  return;
                }
                setPublishOpen(true);
              }}
            >
              <span>
                <Plus size={25} strokeWidth={1.8} />
              </span>
              <strong>发布工作流</strong>
            </button>
          )}
          {publicationsLoading ? (
            <div className="gallery-empty">加载中…</div>
          ) : publicationsError ? (
            <div className="gallery-empty">加载失败，请稍后重试</div>
          ) : galleryItems.length === 0 ? (
            <div className="gallery-empty">
              {workflowTab !== "templates" && !userId
                ? "请先登录后查看"
                : workflowTab === "published"
                  ? "还没有发布，点击左侧卡片开始发布"
                  : workflowTab === "purchased"
                    ? "还没有复制过的工作流"
                    : "暂无公开工作流"}
            </div>
          ) : (
            galleryItems.map((item) => (
              <article
                className="gallery-card"
                key={item.id}
                role="button"
                tabIndex={0}
                onClick={() => setDetailId(item.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setDetailId(item.id);
                  }
                }}
              >
                <GalleryThumb
                  videoUrl={item.videoUrl}
                  coverUrl={withOssImageProcess(item.coverUrl, GALLERY_COVER_OSS_PROCESS)}
                  onMouseEnter={(e) => {
                    const v = e.currentTarget.querySelector("video");
                    if (v) {
                      v.currentTime = 0;
                      void v.play().catch(() => {});
                    }
                  }}
                  onMouseLeave={(e) => {
                    const v = e.currentTarget.querySelector("video");
                    if (v) {
                      v.pause();
                      v.currentTime = 0;
                    }
                  }}
                />
                <div className="gallery-meta">
                  <div className="gallery-author">
                    {item.authorAvatar ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img className="gallery-avatar" src={item.authorAvatar} alt="" />
                    ) : (
                      <span className="gallery-avatar gallery-avatar-fallback" />
                    )}
                    <span>{item.authorName}</span>
                    <BadgeCheck size={14} strokeWidth={2} className="gallery-badge" />
                  </div>
                  <p className="gallery-caption">
                    {item.title ? `《${item.title}》` : ""}
                    {item.description || ""}
                  </p>
                  <p className="gallery-likes" aria-label={`点赞 ${item.likeCount}`}>
                    <Heart
                      size={12}
                      strokeWidth={2}
                      fill={item.liked ? "currentColor" : "none"}
                    />
                    <span>{item.likeCount > 0 ? item.likeCount : "暂无点赞"}</span>
                  </p>
                  {workflowTab === "published" && item.reviewStatus ? (
                    <p className="gallery-caption" style={{ opacity: 0.7, fontSize: 11 }}>
                      {item.reviewStatus === "pending"
                        ? "审核中"
                        : item.reviewStatus === "rejected"
                          ? `已驳回${item.reviewNote ? `：${item.reviewNote}` : ""}`
                          : item.isPublic
                            ? "已公开"
                            : "仅自己可见"}
                    </p>
                  ) : null}
                  {workflowTab === "published" ? (
                    <div className="gallery-card-actions">
                      <button
                        type="button"
                        className="gallery-delete-btn"
                        disabled={deletePublicationMutation.isPending}
                        onClick={(e) => handleDeletePublication(item.id, e)}
                        aria-label={`删除发布 ${item.title || item.id}`}
                      >
                        <Trash2 size={12} strokeWidth={2} />
                        删除
                      </button>
                    </div>
                  ) : null}
                </div>
              </article>
            ))
          )}
        </div>

        {galleryTotalPages > 1 && (
          <nav className="gallery-pagination" aria-label="作品分页">
            <button
              type="button"
              className="gallery-page-btn"
              disabled={galleryPage <= 1}
              onClick={() => changeGalleryPage(galleryPage - 1)}
              aria-label="上一页"
            >
              <ChevronLeft size={16} strokeWidth={2} />
            </button>
            {Array.from({ length: galleryTotalPages }, (_, i) => i + 1).map((page) => (
              <button
                type="button"
                key={page}
                className={`gallery-page-btn${galleryPage === page ? " is-active" : ""}`}
                onClick={() => changeGalleryPage(page)}
                aria-label={`第 ${page} 页`}
                aria-current={galleryPage === page ? "page" : undefined}
              >
                {page}
              </button>
            ))}
            <button
              type="button"
              className="gallery-page-btn"
              disabled={galleryPage >= galleryTotalPages}
              onClick={() => changeGalleryPage(galleryPage + 1)}
              aria-label="下一页"
            >
              <ChevronRight size={16} strokeWidth={2} />
            </button>
          </nav>
        )}
      </section>

      <PublishWorkflowModal
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
        onPublished={() => {
          void queryClient.invalidateQueries({ queryKey: ["workflow-publications"] });
          setWorkflowTab("published");
          setGalleryPage(1);
        }}
      />
      <WorkflowPublicationDetailModal
        publicationId={detailId}
        onClose={() => setDetailId(null)}
        onLikeChange={patchPublicationLike}
      />
      <ViralRemakeDialog open={viralRemakeOpen} onOpenChange={setViralRemakeOpen} />
      <footer>
        <div className="footer-brand">
          <div className="brand">
            <span>{footerConfig?.brandText || "聚梦-专业视频创作平台"}</span>
          </div>
        </div>
        <div className="footer-links">
          <div>
            <strong>产品</strong>
            <a href="https://www.example.com/" target="_blank" rel="noopener noreferrer">
              创作工厂
            </a>
            <a href="https://example.com/" target="_blank" rel="noopener noreferrer">
              剧本工厂
            </a>
          </div>
          <div>
            <strong>关于</strong>
            {footerConfig?.aboutUs?.href ? (
              <a
                href={footerConfig.aboutUs.href}
                target="_blank"
                rel="noopener noreferrer"
              >
                {footerConfig.aboutUs.label || "关于我们"}
              </a>
            ) : (
              <a
                href="#"
                onClick={(e) => {
                  e.preventDefault();
                  toast.message("暂未配置关于我们链接");
                }}
              >
                {footerConfig?.aboutUs?.label || "关于我们"}
              </a>
            )}
            <a
              href="#"
              onClick={(e) => {
                e.preventDefault();
                const qrs = footerConfig?.contactUs?.qrCodes ?? [];
                if (!qrs.length) {
                  toast.message("暂未配置联系二维码");
                  return;
                }
                setContactQrOpen(true);
              }}
            >
              {footerConfig?.contactUs?.label || "联系我们"}
            </a>
          </div>
          <div>
            <strong>关注</strong>
            {(footerConfig?.socialLinks?.length
              ? footerConfig.socialLinks
              : [
                  { id: "xhs", label: "小红书", href: "" },
                  { id: "bili", label: "哔哩哔哩", href: "" },
                  { id: "dy", label: "抖音", href: "" },
                  { id: "sph", label: "视频号", href: "" },
                ]
            ).map((link) =>
              link.href ? (
                <a
                  key={link.id}
                  href={link.href}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {link.label}
                </a>
              ) : (
                <a
                  key={link.id}
                  href="#"
                  onClick={(e) => {
                    e.preventDefault();
                    toast.message(`暂未配置「${link.label}」链接`);
                  }}
                >
                  {link.label}
                </a>
              )
            )}
          </div>
        </div>
        {/* 友情链接：后台配置后展示；前缀 + 紫色横排链接 */}
        {(footerConfig?.friendLinks?.length ?? 0) > 0 ? (
          <nav className="footer-friend-links" aria-label="友情链接">
            <span className="footer-friend-label">
              {footerConfig?.friendLinksLabel || "友情链接"}
            </span>
            {footerConfig!.friendLinks.map((link) => (
              <a
                key={link.id}
                href={link.href}
                target="_blank"
                rel="noopener noreferrer"
              >
                {link.label}
              </a>
            ))}
          </nav>
        ) : null}
        <div className="footer-bottom">
          <div className="footer-bottom-meta">
            <span>{footerConfig?.copyright || "© 2026 JUMENG AI"}</span>
            {/* ICP：无备案号则不展示（开源副本默认空，由部署方在后台填写） */}
            {(() => {
              const icp = (footerConfig?.icpNumber || "").trim();
              if (!icp) return null;
              return (
                <a
                  className="footer-icp"
                  href={
                    (footerConfig?.icpHref || "").trim() ||
                    "https://beian.miit.gov.cn/"
                  }
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {icp}
                </a>
              );
            })()}
          </div>
          <span>{footerConfig?.tagline || "为每一个未被看见的想象而生"}</span>
        </div>
      </footer>

      {/* 联系我们：展示后台配置的多个二维码 */}
      {contactQrOpen ? (
        <div
          className="footer-qr-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={footerConfig?.contactUs?.label || "联系我们"}
          onClick={() => setContactQrOpen(false)}
        >
          <div
            className="footer-qr-panel"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="footer-qr-header">
              <strong>{footerConfig?.contactUs?.label || "联系我们"}</strong>
              <button
                type="button"
                className="footer-qr-close"
                aria-label="关闭"
                onClick={() => setContactQrOpen(false)}
              >
                ×
              </button>
            </div>
            <div className="footer-qr-grid">
              {(footerConfig?.contactUs?.qrCodes ?? []).map((qr) => (
                <div key={qr.id} className="footer-qr-item">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={qr.imageUrl} alt={qr.label || "二维码"} />
                  {qr.label ? <span>{qr.label}</span> : null}
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </HuabuPublicShell>
  );
}
