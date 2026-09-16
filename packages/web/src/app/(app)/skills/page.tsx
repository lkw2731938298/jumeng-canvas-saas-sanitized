"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Pencil, Plus, Search, Star, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { HuabuPublicShell } from "@/components/huabu/HuabuPublicShell";
import { ViralRemakeDialog } from "@/components/huabu/ViralRemakeDialog";
import {
  CreationPromptBox,
  focusCreationPromptBox,
} from "@/components/huabu/CreationPromptBox";
import { CreateSkillFromPromptDialog } from "@/components/huabu/CreateSkillFromPromptDialog";
import { EditSkillMetaDialog } from "@/components/huabu/EditSkillMetaDialog";
import {
  archiveMySkill,
  favoriteSkill,
  getSkillDoc,
  listFavoriteSkills,
  listMySkills,
  listSkills,
  publishMySkill,
  unfavoriteSkill,
  unpublishMySkill,
  type SkillDoc,
  type SkillItem,
} from "@/lib/api/skills";
import { ApiError } from "@/lib/api/client";
import { VIRAL_REMAKE_SKILL } from "@/lib/canvas/viralRemakeSession";
import { OVERSEAS_LOCALIZE_SKILL } from "@/lib/canvas/overseasSession";
import { useAuthStore } from "@/stores/authStore";
import { localMarketingCover } from "@/lib/huabu/localMarketingCover";

/** 技能页：创作框 + Skill 目录（后端权威）；可展开站内 SKILL.md */
export default function SkillsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const [skillTab, setSkillTab] = useState<"skills" | "favorites" | "mine">("skills");
  const [skillCategory, setSkillCategory] = useState("推荐");
  const [skillSearch, setSkillSearch] = useState("");
  const [viralRemakeOpen, setViralRemakeOpen] = useState(false);
  const [createSkillOpen, setCreateSkillOpen] = useState(false);
  const [editSkill, setEditSkill] = useState<SkillItem | null>(null);
  const [docPreview, setDocPreview] = useState<SkillDoc | null>(null);
  const [docLoadingSlug, setDocLoadingSlug] = useState<string | null>(null);

  const catalogQuery = useQuery({
    queryKey: ["skills", "catalog"],
    queryFn: () => listSkills(),
    staleTime: 60_000,
  });

  const favoritesQuery = useQuery({
    queryKey: ["skills", "favorites"],
    queryFn: listFavoriteSkills,
    enabled: Boolean(user) && skillTab === "favorites",
    staleTime: 30_000,
  });

  const mineQuery = useQuery({
    queryKey: ["skills", "mine"],
    queryFn: listMySkills,
    enabled: Boolean(user) && skillTab === "mine",
    staleTime: 30_000,
  });

  const categories = catalogQuery.data?.categories ?? ["推荐"];
  const catalogItems = catalogQuery.data?.items ?? [];

  const visibleSkills = useMemo(() => {
    const keyword = skillSearch.trim().toLowerCase();
    let source: SkillItem[] = catalogItems;
    if (skillTab === "favorites") {
      source = favoritesQuery.data ?? catalogItems.filter((s) => s.favorited);
    } else if (skillTab === "mine") {
      source = mineQuery.data ?? [];
    }
    return source.filter((skill) => {
      const matchesCategory =
        skillTab === "mine" || skillCategory === "推荐" || skill.category === skillCategory;
      return (
        matchesCategory &&
        (!keyword ||
          skill.title.toLowerCase().includes(keyword) ||
          skill.description.toLowerCase().includes(keyword))
      );
    });
  }, [
    skillTab,
    skillCategory,
    skillSearch,
    catalogItems,
    favoritesQuery.data,
    mineQuery.data,
  ]);

  const openSkill = async (skill: SkillItem) => {
    if (!user) {
      router.push("/login?next=%2Fskills");
      return;
    }
    const kind = skill.entryKind || skill.slug;
    const isViral = kind === "viral_remake" || kind === VIRAL_REMAKE_SKILL;
    const isOverseas =
      kind === "overseas_localize" ||
      kind === "overseas" ||
      kind === OVERSEAS_LOCALIZE_SKILL;
    // 爆款复刻 / 一键出海：预选到创作框，上传参考与补说明后再发送建 Session
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
    // 我的 Skill：与出海等一样仅预选到创作框，用户自行写内容 / 加附件后再发送
    if (skill.visibility === "private" || skill.ownerUserId) {
      try {
        sessionStorage.setItem("jumeng:preselect_skill", skill.slug);
      } catch {
        /* ignore */
      }
      focusCreationPromptBox();
      toast.message(`已选择「${skill.title}」，可输入内容并添加附件后发送`);
      return;
    }
    // 其它无 Wizard 入口：聚焦创作框并预选
    try {
      sessionStorage.setItem("jumeng:preselect_skill", skill.slug);
    } catch {
      /* ignore */
    }
    focusCreationPromptBox();
    toast.message(`已选择「${skill.title}」，输入想法后发送即可`);
  };

  const openSkillDoc = async (skill: SkillItem, e: React.MouseEvent) => {
    e.stopPropagation();
    setDocLoadingSlug(skill.slug);
    try {
      const doc = await getSkillDoc(skill.slug);
      if (!doc?.markdown?.trim()) {
        toast.error("该 Skill 暂无正文说明");
        return;
      }
      setDocPreview(doc);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "无法加载 Skill 说明";
      toast.error(msg);
    } finally {
      setDocLoadingSlug(null);
    }
  };

  const toggleFavorite = async (skill: SkillItem, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!user) {
      router.push("/login?next=%2Fskills");
      return;
    }
    if (skill.visibility === "private") {
      toast.message("私有 Skill 无需收藏");
      return;
    }
    try {
      if (skill.favorited) {
        await unfavoriteSkill(skill.slug);
      } else {
        await favoriteSkill(skill.slug);
      }
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "收藏失败");
    }
  };

  const removeMine = async (skill: SkillItem, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`删除「${skill.title}」？`)) return;
    try {
      await archiveMySkill(skill.slug);
      void queryClient.invalidateQueries({ queryKey: ["skills", "mine"] });
      toast.success("已删除");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "删除失败");
    }
  };

  const publishMine = async (skill: SkillItem, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!user) {
      router.push("/login?next=%2Fskills");
      return;
    }
    if (!confirm(`提交发布「${skill.title}」？审核通过后所有用户可在 Skill 目录看到。`)) {
      return;
    }
    try {
      await publishMySkill(skill.slug);
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      toast.success("已提交审核，通过后出现在 Skill 目录");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : err instanceof Error ? err.message : "发布失败");
    }
  };

  const unpublishMine = async (skill: SkillItem, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await unpublishMySkill(skill.slug);
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      toast.success("已撤回发布");
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : err instanceof Error ? err.message : "撤回失败");
    }
  };

  return (
    <HuabuPublicShell>
      <section className="skills-page" aria-label="技能">
        <div className="skills-hero">
          <h1>
            一个 Skill，<span>慢慢打磨你的故事</span>
          </h1>
          <CreationPromptBox
            loginNext="/skills"
            boxClassName="skill-creator"
            onOpenViralRemake={() => setViralRemakeOpen(true)}
          />
        </div>

        <div className="skills-catalog">
          <div className="skills-primary-row">
            <div className="skills-primary-tabs" role="tablist" aria-label="技能分类">
              {[
                { id: "skills", label: "Skill" },
                { id: "favorites", label: "收藏" },
                { id: "mine", label: "我的" },
              ].map((tab) => (
                <button
                  type="button"
                  key={tab.id}
                  className={skillTab === tab.id ? "active" : ""}
                  role="tab"
                  aria-selected={skillTab === tab.id}
                  onClick={() => setSkillTab(tab.id as typeof skillTab)}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="create-skill-button"
              onClick={() => {
                if (!user) {
                  router.push(`/login?next=${encodeURIComponent("/skills")}`);
                  return;
                }
                setCreateSkillOpen(true);
              }}
            >
              <Plus size={17} strokeWidth={2} />
              创建 Skill
            </button>
          </div>

          {skillTab !== "mine" ? (
            <div className="skills-filter-row">
              <div className="skills-category-tabs" role="tablist" aria-label="技能类型">
                {categories.map((category) => (
                  <button
                    type="button"
                    key={category}
                    className={skillCategory === category ? "active" : ""}
                    role="tab"
                    aria-selected={skillCategory === category}
                    onClick={() => setSkillCategory(category)}
                  >
                    {category}
                  </button>
                ))}
              </div>
              <label className="skills-search">
                <input
                  type="search"
                  value={skillSearch}
                  onChange={(e) => setSkillSearch(e.target.value)}
                  placeholder="搜索 Skill"
                  aria-label="搜索技能"
                />
                <Search size={15} strokeWidth={1.8} />
              </label>
            </div>
          ) : (
            <div className="skills-filter-row">
              <label className="skills-search">
                <input
                  type="search"
                  value={skillSearch}
                  onChange={(e) => setSkillSearch(e.target.value)}
                  placeholder="搜索我的 Skill"
                  aria-label="搜索我的技能"
                />
                <Search size={15} strokeWidth={1.8} />
              </label>
            </div>
          )}

          <div className="skills-card-grid">
            {visibleSkills.map((skill) => {
              const isFav = Boolean(skill.favorited);
              const hasEntry = Boolean(skill.entryKind) || skill.visibility === "private";
              const mediaKind = String(
                skill.mediaKind || skill.defaults?.mediaKind || ""
              ).toLowerCase();
              const mediaKindLabel =
                mediaKind === "image"
                  ? "生图"
                  : mediaKind === "text"
                    ? "文本"
                    : mediaKind === "audio"
                      ? "音频"
                      : "短视频";
              const thumbBadge =
                skill.visibility === "private"
                  ? mediaKind === "image"
                    ? "生图"
                    : mediaKind === "text"
                      ? "文本"
                      : mediaKind === "audio"
                        ? "音频"
                        : mediaKind === "video"
                          ? "视频"
                          : "我的"
                  : skill.entryKind
                    ? "视频"
                    : "技能";
              const cardDesc =
                skill.visibility === "private"
                  ? skill.description || skill.skillDocSummary || ""
                  : skill.skillDocSummary || skill.description || "";
              return (
                <article
                  className={
                    skillTab === "mine"
                      ? "skill-library-card skill-library-card-mine"
                      : "skill-library-card"
                  }
                  key={skill.slug}
                  role={hasEntry ? "button" : undefined}
                  tabIndex={hasEntry ? 0 : undefined}
                  onClick={() => void openSkill(skill)}
                  onKeyDown={(e) => {
                    if (!hasEntry) return;
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      openSkill(skill);
                    }
                  }}
                >
                  <div className="skill-library-thumb">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={
                        skill.coverUrl || localMarketingCover(0)
                      }
                      alt=""
                    />
                    <span>{thumbBadge}</span>
                  </div>
                  <div className="skill-library-content">
                    <div className="skill-library-title">
                      <h2>{skill.title}</h2>
                      {skillTab === "mine" ? (
                        <button
                          type="button"
                          aria-label="删除技能"
                          onClick={(e) => void removeMine(skill, e)}
                        >
                          <Trash2 size={14} strokeWidth={1.8} />
                        </button>
                      ) : (
                        <button
                          type="button"
                          className={isFav ? "active" : ""}
                          aria-label={isFav ? "取消收藏" : "收藏技能"}
                          onClick={(e) => void toggleFavorite(skill, e)}
                        >
                          <Star size={14} strokeWidth={1.8} fill={isFav ? "currentColor" : "none"} />
                        </button>
                      )}
                    </div>
                    <p>{cardDesc}</p>
                    {/* 我的 Skill：展示真实可执行步骤与参数徽标 */}
                    {skill.pipelineSummary ? (
                      <p className="skill-pipeline-summary text-xs text-muted-foreground mt-1">
                        {skill.pipelineSummary}
                      </p>
                    ) : null}
                    {skill.visibility === "private" && skill.defaults?.idea ? (
                      <p
                        className="skill-defaults-idea text-xs text-muted-foreground/80 mt-0.5 line-clamp-2"
                        title={skill.defaults.idea}
                      >
                        想法：{skill.defaults.idea}
                      </p>
                    ) : null}
                    {skill.visibility === "private" && skill.defaults ? (
                      <div className="skill-mine-badges">
                        <span className="skill-mine-badge">{mediaKindLabel}</span>
                        {skill.defaults.aspectRatio &&
                        (mediaKind === "image" || mediaKind === "video") ? (
                          <span className="skill-mine-badge">{skill.defaults.aspectRatio}</span>
                        ) : null}
                        {mediaKind === "video" && skill.defaults.durationSec ? (
                          <span className="skill-mine-badge">{skill.defaults.durationSec}s</span>
                        ) : null}
                        {skill.defaults.clarity ? (
                          <span className="skill-mine-badge">{skill.defaults.clarity}</span>
                        ) : null}
                      </div>
                    ) : null}
                    <div
                      className="skill-library-meta"
                      onClick={(e) => e.stopPropagation()}
                      onMouseDown={(e) => e.stopPropagation()}
                    >
                      <span>
                        {skillTab === "mine"
                          ? skill.reviewStatus === "pending"
                            ? "审核中"
                            : skill.reviewStatus === "approved" && skill.visibility === "public"
                              ? "已公开"
                              : skill.reviewStatus === "rejected"
                                ? "已驳回"
                                : "私有 · 点按即跑"
                          : skill.ownerUserId
                            ? skill.ownerDisplayName || "用户"
                            : "聚梦"}
                      </span>
                      {/* 仅「我的」展示预览；公开目录不展示 SKILL.md 入口 */}
                      {skillTab === "mine" ? (
                        <>
                          <button
                            type="button"
                            className="skill-doc-link skill-doc-link-strong"
                            onClick={(e) => {
                              e.stopPropagation();
                              setEditSkill(skill);
                            }}
                          >
                            <Pencil size={11} strokeWidth={2} />
                            编辑
                          </button>
                          <button
                            type="button"
                            className="skill-doc-link skill-doc-link-strong"
                            onClick={(e) => void openSkillDoc(skill, e)}
                          >
                            <BookOpen size={11} strokeWidth={2} />
                            {docLoadingSlug === skill.slug ? "加载中…" : "预览 SKILL.md"}
                          </button>
                        </>
                      ) : null}
                      {/* 公开目录：文案收藏按钮（标题星标亦常显） */}
                      {skillTab !== "mine" && skill.visibility !== "private" ? (
                        <button
                          type="button"
                          className={`skill-fav-link${isFav ? " active" : ""}`}
                          onClick={(e) => void toggleFavorite(skill, e)}
                        >
                          <Star size={11} strokeWidth={2} fill={isFav ? "currentColor" : "none"} />
                          {isFav ? "已收藏" : "收藏"}
                        </button>
                      ) : null}
                      {/* 我的：发布 / 撤回 */}
                      {skillTab === "mine" ? (
                        skill.reviewStatus === "pending" ? (
                          <button
                            type="button"
                            className="skill-publish-link"
                            onClick={(e) => void unpublishMine(skill, e)}
                          >
                            取消审核
                          </button>
                        ) : skill.reviewStatus === "approved" &&
                          skill.visibility === "public" ? (
                          <button
                            type="button"
                            className="skill-publish-link"
                            onClick={(e) => void unpublishMine(skill, e)}
                          >
                            撤回公开
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="skill-publish-link"
                            onClick={(e) => void publishMine(skill, e)}
                          >
                            发布
                          </button>
                        )
                      ) : null}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>

          {visibleSkills.length === 0 && (
            <div className="skills-empty">
              <BookOpen size={34} strokeWidth={1.4} />
              <span>
                {skillTab === "mine"
                  ? "暂无自建 Skill：点「创建 Skill」开始"
                  : skillTab === "favorites"
                    ? "还没有收藏"
                    : "暂无相关 Skill"}
              </span>
            </div>
          )}
        </div>
      </section>
      <ViralRemakeDialog open={viralRemakeOpen} onOpenChange={setViralRemakeOpen} />
      <CreateSkillFromPromptDialog
        open={createSkillOpen}
        onClose={() => setCreateSkillOpen(false)}
        onCreated={(skill) => {
          void queryClient.invalidateQueries({ queryKey: ["skills"] });
          setSkillTab("mine");
          try {
            sessionStorage.setItem("jumeng:preselect_skill", skill.slug);
          } catch {
            /* ignore */
          }
          focusCreationPromptBox();
          toast.message(`已选择「${skill.title}」，可输入内容并添加附件后发送`);
        }}
      />
      <EditSkillMetaDialog
        open={Boolean(editSkill)}
        skill={editSkill}
        onClose={() => setEditSkill(null)}
        onSaved={() => {
          void queryClient.invalidateQueries({ queryKey: ["skills"] });
        }}
      />
      {docPreview ? (
        <div
          className="skill-doc-modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label={`${docPreview.title} 说明`}
          onClick={() => setDocPreview(null)}
        >
          <div
            className="skill-doc-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <header>
              <h2>{docPreview.title}</h2>
              <button type="button" onClick={() => setDocPreview(null)} aria-label="关闭">
                关闭
              </button>
            </header>
            {docPreview.description ? <p className="skill-doc-desc">{docPreview.description}</p> : null}
            <pre className="skill-doc-body">{docPreview.markdown}</pre>
            <footer>
              <button
                type="button"
                className="skill-doc-use"
                onClick={() => {
                  const hit =
                    visibleSkills.find((s) => s.slug === docPreview.slug) ||
                    catalogItems.find((s) => s.slug === docPreview.slug) ||
                    (mineQuery.data ?? []).find((s) => s.slug === docPreview.slug);
                  setDocPreview(null);
                  if (hit) void openSkill(hit);
                }}
              >
                使用此 Skill
              </button>
            </footer>
          </div>
        </div>
      ) : null}
    </HuabuPublicShell>
  );
}

