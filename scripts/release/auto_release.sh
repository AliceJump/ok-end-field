#!/usr/bin/env bash
set -euo pipefail

# 统一 UTF8，避免 GitHub tag 中文乱码
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8
export GIT_EDITOR=${GIT_EDITOR:-vim}

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 版本限制
MAX_MAJOR=10
MAX_MINOR=10
MAX_PATCH=99

# 演习模式
DRY_RUN=false

# 解析参数
for arg in "$@"; do
    case "$arg" in
        -d|--dry-run|--DryRun)
            DRY_RUN=true
            ;;
        *)
            echo -e "${RED}未知参数: $arg${NC}"
            echo "用法: $0 [--dry-run]"
            exit 1
            ;;
    esac
done

# ====== 工具函数 ======

get_all_tags() {
    local tags
    tags=$(git tag -l 2>/dev/null) || {
        echo -e "${RED}✗ 获取标签失败${NC}" >&2
        return 1
    }

    local tag_list=()
    while IFS= read -r tag; do
        [[ -n "$tag" ]] && tag_list+=("$tag")
    done <<< "$tags"

    echo -e "${GREEN}✓ 找到 ${#tag_list[@]} 个标签${NC}" >&2
    echo "${tag_list[@]}"
}

parse_version() {
    local tag="$1"

    if [[ "$tag" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
        local major="${BASH_REMATCH[1]}"
        local minor="${BASH_REMATCH[2]}"
        local patch="${BASH_REMATCH[3]}"

        if (( major <= MAX_MAJOR && minor <= MAX_MINOR && patch <= MAX_PATCH )); then
            echo "$major $minor $patch"
            return 0
        fi
    fi

    return 1
}

find_max_version() {
    local -a tags=("$@")

    local max_major=0 max_minor=0 max_patch=0
    local found=false

    for tag in "${tags[@]}"; do
        local result
        result=$(parse_version "$tag") || continue

        read -r major minor patch <<< "$result"

        if (( major > max_major ||
              (major == max_major && minor > max_minor) ||
              (major == max_major && minor == max_minor && patch > max_patch) )); then
            max_major=$major
            max_minor=$minor
            max_patch=$patch
            found=true
        fi
    done

    if [[ "$found" == false ]]; then
        echo -e "${YELLOW}⚠ 未找到有效版本标签${NC}" >&2
        return 1
    fi

    echo -e "${GREEN}✓ 当前最大版本: v${max_major}.${max_minor}.${max_patch}${NC}" >&2
    echo "$max_major $max_minor $max_patch"
}

increment_version() {
    local major=${1:-0}
    local minor=${2:-0}
    local patch=${3:-0}

    # 如果没有任何版本（传入空值），从 v0.1.0 开始
    if [[ -z "${1:-}" && -z "${2:-}" && -z "${3:-}" ]]; then
        echo "0 1 0"
        return 0
    fi

    if (( patch < MAX_PATCH )); then
        echo "$major $minor $((patch + 1))"
        return 0
    fi

    if (( minor < MAX_MINOR )); then
        echo "$major $((minor + 1)) 0"
        return 0
    fi

    if (( major < MAX_MAJOR )); then
        echo "$((major + 1)) 0 0"
        return 0
    fi

    echo -e "${RED}✗ 版本号已达到最大值 v${MAX_MAJOR}.${MAX_MINOR}.${MAX_PATCH}${NC}" >&2
    return 1
}

format_version() {
    local major=$1 minor=$2 patch=$3
    echo "v${major}.${minor}.${patch}"
}

test_git_status() {
    local status
    status=$(git status --porcelain 2>&1)

    if [[ -n "$status" ]]; then
        echo -e "${YELLOW}⚠ 工作区有未提交更改：${NC}"
        echo "$status"
        read -r -p "是否继续？(y/N): " response
        [[ "$response" == "y" || "$response" == "Y" ]]
        return $?
    fi

    return 0
}

get_latest_commit_message() {
    local message
    message=$(git log -1 --pretty=%B 2>/dev/null) || {
        echo "Release"
        return 0
    }
    echo "$message" | head -1
}

new_git_tag() {
    local tag="$1"
    local message="$2"

    if git tag -a "$tag" -m "$message" 2>/dev/null; then
        echo -e "${GREEN}✓ 已创建标签: ${tag}${NC}"
        return 0
    else
        echo -e "${RED}✗ 创建标签失败${NC}" >&2
        return 1
    fi
}

push_git_tag() {
    local tag="$1"

    if git push origin "$tag" 2>/dev/null; then
        echo -e "${GREEN}✓ 标签已推送到远程: ${tag}${NC}"
        return 0
    else
        echo -e "${RED}✗ 推送标签失败${NC}" >&2
        return 1
    fi
}

# ====== 主流程 ======

echo ""
printf "%60s\n" | tr ' ' '='
echo -e "${CYAN}自动版本发布脚本${NC}"
printf "%60s\n" | tr ' ' '='

# 1. 获取所有标签
tags=$(get_all_tags)
# get_all_tags 已将状态信息输出到 stderr，stdout 只有标签列表
read -ra tag_array <<< "$tags"

# 2. 查找最大版本
max_result=$(find_max_version "${tag_array[@]}") || {
    max_result=""
}

if [[ -n "$max_result" ]]; then
    read -r max_major max_minor max_patch <<< "$max_result"
else
    # 没有找到有效版本，从头开始
    max_major=""
    max_minor=""
    max_patch=""
    echo -e "${YELLOW}⚠ 将从头创建初始版本${NC}"
fi

# 3. 计算下一个版本
next_result=$(increment_version "${max_major:-}" "${max_minor:-}" "${max_patch:-}") || exit 1
read -r next_major next_minor next_patch <<< "$next_result"
next_tag=$(format_version "$next_major" "$next_minor" "$next_patch")

echo ""
echo -e "${CYAN}📦 新版本: ${next_tag}${NC}"
echo ""

# 4. 演习模式
if [[ "$DRY_RUN" == true ]]; then
    echo -e "${YELLOW}🔍 演习模式：${NC}"
    echo "  创建标签: $next_tag"
    echo "  推送命令: git push origin $next_tag"
    exit 0
fi

# 5. 检查工作区状态
if ! test_git_status; then
    exit 1
fi

# 6. 确认
echo -e "${YELLOW}即将发布版本: ${next_tag}${NC}"
read -r -p "确认继续？(Y/n): " response
if [[ "$response" == "n" || "$response" == "N" ]]; then
    echo -e "${RED}✗ 操作已取消${NC}"
    exit 1
fi

echo ""
echo -e "${CYAN}⟳ 拉取远程最新提交...${NC}"

if ! git pull --rebase 2>/dev/null; then
    echo -e "${RED}✗ 拉取失败（可能存在冲突）${NC}" >&2
    exit 1
fi

echo -e "${GREEN}✓ 已同步远程提交${NC}"

echo ""
echo -e "${CYAN}⟳ 推送最新 commit...${NC}"

if ! git push 2>/dev/null; then
    echo -e "${RED}✗ 推送 commit 失败${NC}" >&2
    exit 1
fi

echo -e "${GREEN}✓ commit 已推送${NC}"

message=$(get_latest_commit_message)

if ! new_git_tag "$next_tag" "$message"; then
    exit 1
fi

if ! push_git_tag "$next_tag"; then
    echo -e "${YELLOW}⚠ 标签已创建但推送失败，可手动执行：${NC}"
    echo "git push origin $next_tag"
    exit 1
fi

echo ""
printf "%60s\n" | tr ' ' '='
echo -e "${GREEN}✓ 发布成功！版本: ${next_tag}${NC}"
printf "%60s\n" | tr ' ' '='
