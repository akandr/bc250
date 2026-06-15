#!/usr/bin/env bash
# bc250-occupancy.sh — measure achieved GDDR6 streaming bandwidth as a
# function of launched parallelism (occupancy / memory-level parallelism).
#
# Why: the roofline microbenchmark (bc250-roofline.sh) reports a 357 GB/s
# *peak* streaming bandwidth, yet a single autoregressive-decode stream
# reaches only ~53% of it. This probe resolves whether that gap is a
# bandwidth ceiling or a latency/occupancy effect: it streams a FIXED
# 1 GiB working set (read+write of a 512 MiB host-coherent buffer in the
# UMA address space) with a grid-stride copy kernel, and sweeps the number
# of launched workgroups (= active wavefronts = occupancy). Total bytes
# moved is constant; only the parallelism varies. If achieved bandwidth
# rises from ~50% at low occupancy toward the ~357 GB/s peak at high
# occupancy, the single-stream shortfall is a Little's-law latency effect
# (BW = MLP / latency), not a saturated bus.
#
# Operating point must match the roofline run: stock 24-CU,
# oberon-governor 1500 MHz cap, host-coherent UMA memory.
#
# Outputs one line per occupancy level to stdout:
#   OCC groups=<int> threads=<int> wave32=<int> dispatch_s=<best> gbs=<best> frac_peak=<f>
# plus a trailing RESULT line. BW_PEAK_GBS is taken as the max observed.

set -euo pipefail

BUF_FLOATS=134217728   # 128 Mi floats = 512 MiB per buffer; 1 GiB moved per dispatch
PASSES=7               # passes per occupancy level; best (max GB/s) is kept
LOCAL_SIZE=256         # threads per workgroup (matches roofline harness)
PEAK_GBS=357.29        # roofline peak for fraction-of-peak reporting

# Workgroup sweep: 1 .. 524288 (524288 WG x 256 = 134,217,728 threads = one
# per element = full occupancy). Powers of two plus a few intermediates.
GROUPS_SWEEP="1 2 4 8 16 24 32 48 64 96 128 192 256 384 512 768 1024 2048 4096 8192 16384 32768 65536 131072 262144 524288"

KEEP_TMP=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --keep-tmp) KEEP_TMP=1; shift ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

command -v glslangValidator >/dev/null 2>&1 || { echo "ERROR: glslangValidator not found" >&2; exit 1; }
command -v gcc >/dev/null 2>&1 || { echo "ERROR: gcc not found" >&2; exit 1; }

TMPDIR="$(mktemp -d)"
[ "$KEEP_TMP" -eq 0 ] && trap 'rm -rf "$TMPDIR"' EXIT

# ─── Grid-stride streaming-copy shader ────────────────────────────────────────
# Each thread copies src[i]->dst[i] for i = gid, gid+stride, ... covering all n.
# stride = (#workgroups * local_size), so fewer workgroups => fewer concurrent
# threads => lower occupancy, but the SAME total 2*n*4 bytes are moved.
cat >"$TMPDIR/stream.comp" <<'GLSL'
#version 450
layout(local_size_x = 256) in;
layout(std430, binding = 0) readonly  buffer Src { float src[]; };
layout(std430, binding = 1) writeonly buffer Dst { float dst[]; };
layout(push_constant) uniform PC { uint n; } pc;
void main() {
    uint stride = gl_NumWorkGroups.x * 256u;
    for (uint i = gl_GlobalInvocationID.x; i < pc.n; i += stride) {
        dst[i] = src[i];
    }
}
GLSL

# ─── C Vulkan driver (occupancy sweep) ────────────────────────────────────────
cat >"$TMPDIR/occ.c" <<'C'
#define _POSIX_C_SOURCE 200809L
#include <vulkan/vulkan.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <inttypes.h>

#define CHECK(call) do { \
    VkResult _r = (call); \
    if (_r != VK_SUCCESS) { fprintf(stderr, "%s failed: %d line %d\n", #call, _r, __LINE__); exit(1); } \
} while(0)

static double now_sec(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}
static int read_file(const char *p, char **buf, size_t *sz) {
    FILE *f = fopen(p, "rb"); if (!f) return 1;
    fseek(f, 0, SEEK_END); long n = ftell(f); rewind(f);
    *buf = malloc(n); *sz = n; fread(*buf, 1, n, f); fclose(f); return 0;
}
static uint32_t find_mem(VkPhysicalDevice pd, uint32_t bits, VkMemoryPropertyFlags want) {
    VkPhysicalDeviceMemoryProperties p; vkGetPhysicalDeviceMemoryProperties(pd, &p);
    for (uint32_t i = 0; i < p.memoryTypeCount; i++)
        if ((bits & (1u<<i)) && (p.memoryTypes[i].propertyFlags & want) == want) return i;
    return UINT32_MAX;
}
static void alloc_buf(VkDevice dev, VkPhysicalDevice pd, VkDeviceSize bytes,
                       VkBuffer *buf, VkDeviceMemory *mem, void **map) {
    VkBufferCreateInfo bci = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bci.size = bytes; bci.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
    bci.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    CHECK(vkCreateBuffer(dev, &bci, NULL, buf));
    VkMemoryRequirements req; vkGetBufferMemoryRequirements(dev, *buf, &req);
    uint32_t mi = find_mem(pd, req.memoryTypeBits,
                           VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                           VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (mi == UINT32_MAX) {
        mi = find_mem(pd, req.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
        if (mi == UINT32_MAX) { fprintf(stderr, "no suitable memory\n"); exit(1); }
    }
    VkMemoryAllocateInfo mai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    mai.allocationSize = req.size; mai.memoryTypeIndex = mi;
    CHECK(vkAllocateMemory(dev, &mai, NULL, mem));
    CHECK(vkBindBufferMemory(dev, *buf, *mem, 0));
    if (map) { VkResult r = vkMapMemory(dev, *mem, 0, bytes, 0, map); if (r != VK_SUCCESS) *map = NULL; }
}
static VkShaderModule load_shader(VkDevice dev, const char *path) {
    char *buf; size_t sz;
    if (read_file(path, &buf, &sz)) { fprintf(stderr, "can't read %s\n", path); exit(1); }
    VkShaderModuleCreateInfo ci = {VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    ci.codeSize = sz; ci.pCode = (const uint32_t*)buf;
    VkShaderModule m; CHECK(vkCreateShaderModule(dev, &ci, NULL, &m)); free(buf); return m;
}
static double dispatch_time(VkDevice dev, VkQueue q, VkCommandPool pool, VkFence fence,
                             VkPipeline pipe, VkPipelineLayout layout, VkDescriptorSet ds,
                             uint32_t groups_x, const void *pc, uint32_t pc_size) {
    VkCommandBufferAllocateInfo ai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    ai.commandPool = pool; ai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY; ai.commandBufferCount = 1;
    VkCommandBuffer cmd; CHECK(vkAllocateCommandBuffers(dev, &ai, &cmd));
    VkCommandBufferBeginInfo bi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    CHECK(vkBeginCommandBuffer(cmd, &bi));
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipe);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, layout, 0, 1, &ds, 0, NULL);
    if (pc && pc_size) vkCmdPushConstants(cmd, layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, pc_size, pc);
    vkCmdDispatch(cmd, groups_x, 1, 1);
    CHECK(vkEndCommandBuffer(cmd));
    VkSubmitInfo si = {VK_STRUCTURE_TYPE_SUBMIT_INFO};
    si.commandBufferCount = 1; si.pCommandBuffers = &cmd;
    vkResetFences(dev, 1, &fence);
    double t0 = now_sec();
    CHECK(vkQueueSubmit(q, 1, &si, fence));
    CHECK(vkWaitForFences(dev, 1, &fence, VK_TRUE, UINT64_MAX));
    double t1 = now_sec();
    vkFreeCommandBuffers(dev, pool, 1, &cmd);
    return t1 - t0;
}

int main(int argc, char **argv) {
    if (argc < 6) {
        fprintf(stderr, "usage: %s stream.spv n_floats passes peak_gbs g1 [g2 ...]\n", argv[0]);
        return 2;
    }
    const char *spv = argv[1];
    uint64_t n      = strtoull(argv[2], NULL, 0);
    uint32_t passes = (uint32_t)atoi(argv[3]);
    double peak     = atof(argv[4]);
    int ng = argc - 5;
    uint32_t *groups = malloc(sizeof(uint32_t) * ng);
    for (int i = 0; i < ng; i++) groups[i] = (uint32_t)strtoul(argv[5+i], NULL, 0);

    VkInstance inst;
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO}; app.apiVersion = VK_API_VERSION_1_1;
    VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO}; ici.pApplicationInfo = &app;
    CHECK(vkCreateInstance(&ici, NULL, &inst));
    VkPhysicalDevice pds[16]; uint32_t pd_count = 16;
    CHECK(vkEnumeratePhysicalDevices(inst, &pd_count, pds));
    VkPhysicalDevice pd = pds[0];
    for (uint32_t i = 0; i < pd_count; i++) {
        VkPhysicalDeviceProperties p; vkGetPhysicalDeviceProperties(pds[i], &p);
        if (p.vendorID == 0x1002) { pd = pds[i]; break; }
    }
    VkPhysicalDeviceProperties pd_props; vkGetPhysicalDeviceProperties(pd, &pd_props);
    fprintf(stderr, "device: %s\n", pd_props.deviceName);

    uint32_t qf = UINT32_MAX;
    VkQueueFamilyProperties qfp[32]; uint32_t qc = 32;
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &qc, qfp);
    for (uint32_t i = 0; i < qc; i++) if (qfp[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { qf = i; break; }
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    qci.queueFamilyIndex = qf; qci.queueCount = 1; qci.pQueuePriorities = &prio;
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    dci.queueCreateInfoCount = 1; dci.pQueueCreateInfos = &qci;
    VkDevice dev; CHECK(vkCreateDevice(pd, &dci, NULL, &dev));
    VkQueue q; vkGetDeviceQueue(dev, qf, 0, &q);

    VkDeviceSize bytes = n * sizeof(float);
    VkBuffer src, dst; VkDeviceMemory msrc, mdst; void *src_map;
    alloc_buf(dev, pd, bytes, &src, &msrc, &src_map);
    alloc_buf(dev, pd, bytes, &dst, &mdst, NULL);
    if (src_map) for (uint64_t i = 0; i < n; i++) ((float*)src_map)[i] = (float)i * 1e-9f;

    VkDescriptorSetLayoutBinding binds[2] = {0};
    binds[0].binding = 0; binds[0].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    binds[0].descriptorCount = 1; binds[0].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    binds[1] = binds[0]; binds[1].binding = 1;
    VkDescriptorSetLayoutCreateInfo dsli = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    dsli.bindingCount = 2; dsli.pBindings = binds;
    VkDescriptorSetLayout dsl; CHECK(vkCreateDescriptorSetLayout(dev, &dsli, NULL, &dsl));
    VkPushConstantRange pcr = {VK_SHADER_STAGE_COMPUTE_BIT, 0, 4};
    VkPipelineLayoutCreateInfo plci = {VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    plci.setLayoutCount = 1; plci.pSetLayouts = &dsl;
    plci.pushConstantRangeCount = 1; plci.pPushConstantRanges = &pcr;
    VkPipelineLayout layout; CHECK(vkCreatePipelineLayout(dev, &plci, NULL, &layout));

    VkDescriptorPoolSize ps = {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 2};
    VkDescriptorPoolCreateInfo dpci = {VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    dpci.maxSets = 1; dpci.poolSizeCount = 1; dpci.pPoolSizes = &ps;
    VkDescriptorPool pool; CHECK(vkCreateDescriptorPool(dev, &dpci, NULL, &pool));
    VkDescriptorSetAllocateInfo dsai = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    dsai.descriptorPool = pool; dsai.descriptorSetCount = 1; dsai.pSetLayouts = &dsl;
    VkDescriptorSet ds; CHECK(vkAllocateDescriptorSets(dev, &dsai, &ds));
    VkDescriptorBufferInfo dbi[2] = { {src, 0, bytes}, {dst, 0, bytes} };
    VkWriteDescriptorSet wds[2] = {0};
    for (int i = 0; i < 2; i++) {
        wds[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        wds[i].dstSet = ds; wds[i].dstBinding = i; wds[i].descriptorCount = 1;
        wds[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER; wds[i].pBufferInfo = &dbi[i];
    }
    vkUpdateDescriptorSets(dev, 2, wds, 0, NULL);

    VkShaderModule shader = load_shader(dev, spv);
    VkComputePipelineCreateInfo cpci = {VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    cpci.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    cpci.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
    cpci.stage.module = shader; cpci.stage.pName = "main"; cpci.layout = layout;
    VkPipeline pipe; CHECK(vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &cpci, NULL, &pipe));

    VkCommandPoolCreateInfo cpi = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    cpi.queueFamilyIndex = qf;
    VkCommandPool cmd_pool; CHECK(vkCreateCommandPool(dev, &cpi, NULL, &cmd_pool));
    VkFenceCreateInfo fci = {VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence fence; CHECK(vkCreateFence(dev, &fci, NULL, &fence));

    uint32_t pc_n = (uint32_t)n;
    double moved = 2.0 * (double)bytes;   /* read + write, constant across sweep */

    /* warmup at full occupancy */
    dispatch_time(dev, q, cmd_pool, fence, pipe, layout, ds, (uint32_t)(n/256), &pc_n, 4);

    printf("N_FLOATS=%" PRIu64 " BYTES_MOVED=%.0f LOCAL_SIZE=256\n", n, moved);
    double best_overall = 0.0;
    for (int gi = 0; gi < ng; gi++) {
        uint32_t g = groups[gi];
        double best = 0.0;
        for (uint32_t p = 0; p < passes; p++) {
            double t = dispatch_time(dev, q, cmd_pool, fence, pipe, layout, ds, g, &pc_n, 4);
            double gbs = moved / t / 1e9;
            if (gbs > best) best = gbs;
        }
        if (best > best_overall) best_overall = best;
        uint64_t threads = (uint64_t)g * 256u;
        printf("OCC groups=%u threads=%" PRIu64 " wave32=%" PRIu64 " gbs=%.2f frac_peak=%.4f\n",
               g, threads, threads/32u, best, best/peak);
        fflush(stdout);
    }
    printf("RESULT bw_max_gbs=%.2f peak_ref_gbs=%.2f\n", best_overall, peak);

    vkDestroyFence(dev, fence, NULL); vkDestroyCommandPool(dev, cmd_pool, NULL);
    vkDestroyPipeline(dev, pipe, NULL); vkDestroyShaderModule(dev, shader, NULL);
    vkDestroyDescriptorPool(dev, pool, NULL); vkDestroyPipelineLayout(dev, layout, NULL);
    vkDestroyDescriptorSetLayout(dev, dsl, NULL);
    vkFreeMemory(dev, msrc, NULL); vkFreeMemory(dev, mdst, NULL);
    vkDestroyBuffer(dev, src, NULL); vkDestroyBuffer(dev, dst, NULL);
    vkDestroyDevice(dev, NULL); vkDestroyInstance(inst, NULL);
    free(groups);
    return 0;
}
C

echo "Compiling shader..." >&2
glslangValidator -V "$TMPDIR/stream.comp" -o "$TMPDIR/stream.spv" >/dev/null
echo "Compiling Vulkan driver..." >&2
gcc -std=c11 -O2 -Wall -Wextra -o "$TMPDIR/occ" "$TMPDIR/occ.c" -lvulkan -lm
echo "Running occupancy sweep..." >&2
# shellcheck disable=SC2086
"$TMPDIR/occ" "$TMPDIR/stream.spv" "$BUF_FLOATS" "$PASSES" "$PEAK_GBS" $GROUPS_SWEEP
