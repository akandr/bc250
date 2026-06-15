#!/usr/bin/env bash
# bc250-roofline.sh — measure peak GDDR6 memory bandwidth and peak FP32 throughput
# via two Vulkan compute microbenchmarks.
#
# Outputs one JSON line to stdout:
#   {"bw_peak_gbs": <float>, "fp32_peak_gflops": <float>,
#    "bw_dispatch_s": [...], "fp32_dispatch_s": [...],
#    "bw_buf_bytes": <int>, "fp32_total_gflop": <float>}
#
# The BW benchmark streams a copy (read + write) of BW_BUF_FLOATS×4 bytes.
# The FP32 benchmark runs N_ELEMENTS × FP_CHAINS × FP_ITERS × 2 FMAs.

set -euo pipefail

BW_BUF_FLOATS=134217728   # 128 Mi floats = 512 MiB per buffer; 1 GiB moved per dispatch
BW_PASSES=5
FP_ELEMENTS=8388608        # 8 Mi threads
FP_CHAINS=8                # independent FMA chains per thread (fills SIMD)
FP_ITERS=2048              # iterations per chain
FP_PASSES=4

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

# ─── Bandwidth shader: streaming copy ─────────────────────────────────────────
cat >"$TMPDIR/bw_copy.comp" <<'GLSL'
#version 450
layout(local_size_x = 256) in;
layout(std430, binding = 0) readonly  buffer Src { float src[]; };
layout(std430, binding = 1) writeonly buffer Dst { float dst[]; };
void main() {
    uint i = gl_GlobalInvocationID.x;
    dst[i] = src[i];
}
GLSL

# ─── FP32 throughput shader: 8 independent FMA chains ─────────────────────────
# FP_CHAINS and FP_ITERS are baked in at compile time via specialisation constants.
cat >"$TMPDIR/fp32_mad.comp" <<'GLSL'
#version 450
layout(local_size_x = 256) in;
layout(std430, binding = 0) writeonly buffer Out { float result[]; };
layout(push_constant) uniform PC { uint n; uint iters; } pc;
void main() {
    uint i  = gl_GlobalInvocationID.x;
    float a0 = float(i) * 1.0e-7 + 1.0;
    float a1 = float(i) * 1.1e-7 + 2.0;
    float a2 = float(i) * 1.2e-7 + 3.0;
    float a3 = float(i) * 1.3e-7 + 4.0;
    float a4 = float(i) * 1.4e-7 + 5.0;
    float a5 = float(i) * 1.5e-7 + 6.0;
    float a6 = float(i) * 1.6e-7 + 7.0;
    float a7 = float(i) * 1.7e-7 + 8.0;
    for (uint j = 0u; j < pc.iters; ++j) {
        a0 = fma(a0, 1.00001, 0.000001);
        a1 = fma(a1, 1.00001, 0.000002);
        a2 = fma(a2, 1.00001, 0.000003);
        a3 = fma(a3, 1.00001, 0.000004);
        a4 = fma(a4, 1.00001, 0.000005);
        a5 = fma(a5, 1.00001, 0.000006);
        a6 = fma(a6, 1.00001, 0.000007);
        a7 = fma(a7, 1.00001, 0.000008);
    }
    result[i] = a0 + a1 + a2 + a3 + a4 + a5 + a6 + a7;
}
GLSL

# ─── C Vulkan driver ──────────────────────────────────────────────────────────
cat >"$TMPDIR/roofline.c" <<'C'
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
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

static int read_file(const char *p, char **buf, size_t *sz) {
    FILE *f = fopen(p, "rb");
    if (!f) return 1;
    fseek(f, 0, SEEK_END);
    long n = ftell(f); rewind(f);
    *buf = malloc(n); *sz = n;
    fread(*buf, 1, n, f); fclose(f);
    return 0;
}

static uint32_t find_mem(VkPhysicalDevice pd, uint32_t bits, VkMemoryPropertyFlags want) {
    VkPhysicalDeviceMemoryProperties p;
    vkGetPhysicalDeviceMemoryProperties(pd, &p);
    for (uint32_t i = 0; i < p.memoryTypeCount; i++)
        if ((bits & (1u<<i)) && (p.memoryTypes[i].propertyFlags & want) == want)
            return i;
    return UINT32_MAX;
}

/* alloc + map a host-visible buffer */
static void alloc_buf(VkDevice dev, VkPhysicalDevice pd, VkDeviceSize bytes,
                       VkBuffer *buf, VkDeviceMemory *mem, void **map) {
    VkBufferCreateInfo bci = {VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    bci.size = bytes; bci.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
    bci.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    CHECK(vkCreateBuffer(dev, &bci, NULL, buf));
    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(dev, *buf, &req);
    uint32_t mi = find_mem(pd, req.memoryTypeBits,
                           VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT |
                           VK_MEMORY_PROPERTY_HOST_COHERENT_BIT);
    if (mi == UINT32_MAX) {
        /* fallback: device-local only (no map) – just allocate */
        mi = find_mem(pd, req.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
        if (mi == UINT32_MAX) { fprintf(stderr, "no suitable memory\n"); exit(1); }
    }
    VkMemoryAllocateInfo mai = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    mai.allocationSize = req.size; mai.memoryTypeIndex = mi;
    CHECK(vkAllocateMemory(dev, &mai, NULL, mem));
    CHECK(vkBindBufferMemory(dev, *buf, *mem, 0));
    if (map) {
        VkResult r = vkMapMemory(dev, *mem, 0, bytes, 0, map);
        if (r != VK_SUCCESS) *map = NULL;
    }
}

static VkShaderModule load_shader(VkDevice dev, const char *path) {
    char *buf; size_t sz;
    if (read_file(path, &buf, &sz)) { fprintf(stderr, "can't read %s\n", path); exit(1); }
    VkShaderModuleCreateInfo ci = {VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    ci.codeSize = sz; ci.pCode = (const uint32_t*)buf;
    VkShaderModule m;
    CHECK(vkCreateShaderModule(dev, &ci, NULL, &m));
    free(buf);
    return m;
}

static VkPipeline make_pipeline(VkDevice dev, VkShaderModule shader,
                                VkPipelineLayout layout) {
    VkComputePipelineCreateInfo ci = {VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    ci.stage.sType  = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    ci.stage.stage  = VK_SHADER_STAGE_COMPUTE_BIT;
    ci.stage.module = shader; ci.stage.pName = "main";
    ci.layout = layout;
    VkPipeline p;
    CHECK(vkCreateComputePipelines(dev, VK_NULL_HANDLE, 1, &ci, NULL, &p));
    return p;
}

static double dispatch_time(VkDevice dev, VkQueue q, VkCommandPool pool,
                             VkFence fence, VkPipeline pipe, VkPipelineLayout layout,
                             VkDescriptorSet ds, uint32_t groups_x,
                             const void *pc, uint32_t pc_size) {
    VkCommandBufferAllocateInfo ai = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    ai.commandPool = pool; ai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    ai.commandBufferCount = 1;
    VkCommandBuffer cmd;
    CHECK(vkAllocateCommandBuffers(dev, &ai, &cmd));
    VkCommandBufferBeginInfo bi = {VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    CHECK(vkBeginCommandBuffer(cmd, &bi));
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipe);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, layout,
                            0, 1, &ds, 0, NULL);
    if (pc && pc_size)
        vkCmdPushConstants(cmd, layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, pc_size, pc);
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
    if (argc != 9) {
        fprintf(stderr,
            "usage: %s bw.spv fp.spv bw_floats bw_passes fp_elems fp_chains fp_iters fp_passes\n",
            argv[0]);
        return 2;
    }
    const char *bw_spv  = argv[1];
    const char *fp_spv  = argv[2];
    uint64_t bw_floats  = (uint64_t)strtoull(argv[3], NULL, 0);
    uint32_t bw_passes  = (uint32_t)atoi(argv[4]);
    uint32_t fp_elems   = (uint32_t)atoi(argv[5]);
    uint32_t fp_chains  = (uint32_t)atoi(argv[6]);
    uint32_t fp_iters   = (uint32_t)atoi(argv[7]);
    uint32_t fp_passes  = (uint32_t)atoi(argv[8]);

    VkInstance inst;
    VkApplicationInfo app = {VK_STRUCTURE_TYPE_APPLICATION_INFO};
    app.apiVersion = VK_API_VERSION_1_1;
    VkInstanceCreateInfo ici = {VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    ici.pApplicationInfo = &app;
    CHECK(vkCreateInstance(&ici, NULL, &inst));

    VkPhysicalDevice pds[16]; uint32_t pd_count = 16;
    CHECK(vkEnumeratePhysicalDevices(inst, &pd_count, pds));
    VkPhysicalDevice pd = pds[0];
    for (uint32_t i = 0; i < pd_count; i++) {
        VkPhysicalDeviceProperties p;
        vkGetPhysicalDeviceProperties(pds[i], &p);
        if (p.vendorID == 0x1002) { pd = pds[i]; break; }
    }

    VkPhysicalDeviceProperties pd_props;
    vkGetPhysicalDeviceProperties(pd, &pd_props);
    fprintf(stderr, "device: %s\n", pd_props.deviceName);

    uint32_t qf = UINT32_MAX;
    VkQueueFamilyProperties qfp[32]; uint32_t qc = 32;
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &qc, qfp);
    for (uint32_t i = 0; i < qc; i++)
        if (qfp[i].queueFlags & VK_QUEUE_COMPUTE_BIT) { qf = i; break; }

    float prio = 1.0f;
    VkDeviceQueueCreateInfo qci = {VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    qci.queueFamilyIndex = qf; qci.queueCount = 1; qci.pQueuePriorities = &prio;
    VkDeviceCreateInfo dci = {VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    dci.queueCreateInfoCount = 1; dci.pQueueCreateInfos = &qci;
    VkDevice dev; CHECK(vkCreateDevice(pd, &dci, NULL, &dev));
    VkQueue q; vkGetDeviceQueue(dev, qf, 0, &q);

    /* ── BW buffers ──────────────────────────────────────────────── */
    VkDeviceSize bw_bytes = bw_floats * sizeof(float);
    VkBuffer bw_src, bw_dst; VkDeviceMemory bw_msrc, bw_mdst;
    void *bw_src_map;
    alloc_buf(dev, pd, bw_bytes, &bw_src, &bw_msrc, &bw_src_map);
    alloc_buf(dev, pd, bw_bytes, &bw_dst, &bw_mdst, NULL);
    if (bw_src_map)
        for (uint64_t i = 0; i < bw_floats; i++) ((float*)bw_src_map)[i] = (float)i * 1e-9f;

    /* ── FP buffer ───────────────────────────────────────────────── */
    VkDeviceSize fp_bytes = (VkDeviceSize)fp_elems * sizeof(float);
    VkBuffer fp_buf; VkDeviceMemory fp_mem;
    alloc_buf(dev, pd, fp_bytes, &fp_buf, &fp_mem, NULL);

    /* ── Descriptor set layout helpers ──────────────────────────── */
    /* BW: 2 bindings (src, dst) */
    VkDescriptorSetLayoutBinding bw_binds[2] = {0};
    bw_binds[0].binding = 0; bw_binds[0].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    bw_binds[0].descriptorCount = 1; bw_binds[0].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    bw_binds[1] = bw_binds[0]; bw_binds[1].binding = 1;
    VkDescriptorSetLayoutCreateInfo bw_dsli = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    bw_dsli.bindingCount = 2; bw_dsli.pBindings = bw_binds;
    VkDescriptorSetLayout bw_dsl;
    CHECK(vkCreateDescriptorSetLayout(dev, &bw_dsli, NULL, &bw_dsl));

    /* FP: 1 binding (out) + push constant */
    VkDescriptorSetLayoutBinding fp_bind = bw_binds[0];
    VkDescriptorSetLayoutCreateInfo fp_dsli = bw_dsli;
    fp_dsli.bindingCount = 1; fp_dsli.pBindings = &fp_bind;
    VkDescriptorSetLayout fp_dsl;
    CHECK(vkCreateDescriptorSetLayout(dev, &fp_dsli, NULL, &fp_dsl));

    VkPushConstantRange pcr = {VK_SHADER_STAGE_COMPUTE_BIT, 0, 8};

    VkPipelineLayoutCreateInfo plci = {VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    plci.setLayoutCount = 1; plci.pSetLayouts = &bw_dsl;
    VkPipelineLayout bw_layout;
    CHECK(vkCreatePipelineLayout(dev, &plci, NULL, &bw_layout));

    plci.pSetLayouts = &fp_dsl;
    plci.pushConstantRangeCount = 1; plci.pPushConstantRanges = &pcr;
    VkPipelineLayout fp_layout;
    CHECK(vkCreatePipelineLayout(dev, &plci, NULL, &fp_layout));

    VkDescriptorPoolSize ps = {VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 4};
    VkDescriptorPoolCreateInfo dpci = {VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    dpci.maxSets = 2; dpci.poolSizeCount = 1; dpci.pPoolSizes = &ps;
    VkDescriptorPool pool;
    CHECK(vkCreateDescriptorPool(dev, &dpci, NULL, &pool));

    VkDescriptorSetAllocateInfo dsai = {VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    dsai.descriptorPool = pool; dsai.descriptorSetCount = 1;
    VkDescriptorSet bw_ds, fp_ds;
    dsai.pSetLayouts = &bw_dsl; CHECK(vkAllocateDescriptorSets(dev, &dsai, &bw_ds));
    dsai.pSetLayouts = &fp_dsl; CHECK(vkAllocateDescriptorSets(dev, &dsai, &fp_ds));

    /* wire bw descriptor */
    VkDescriptorBufferInfo bw_dbi[2] = {
        {bw_src, 0, bw_bytes}, {bw_dst, 0, bw_bytes}
    };
    VkWriteDescriptorSet bw_wds[2] = {0};
    for (int i = 0; i < 2; i++) {
        bw_wds[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        bw_wds[i].dstSet = bw_ds; bw_wds[i].dstBinding = i;
        bw_wds[i].descriptorCount = 1;
        bw_wds[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        bw_wds[i].pBufferInfo = &bw_dbi[i];
    }
    vkUpdateDescriptorSets(dev, 2, bw_wds, 0, NULL);

    /* wire fp descriptor */
    VkDescriptorBufferInfo fp_dbi = {fp_buf, 0, fp_bytes};
    VkWriteDescriptorSet fp_wds = {VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
    fp_wds.dstSet = fp_ds; fp_wds.descriptorCount = 1;
    fp_wds.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    fp_wds.pBufferInfo = &fp_dbi;
    vkUpdateDescriptorSets(dev, 1, &fp_wds, 0, NULL);

    VkShaderModule bw_shader = load_shader(dev, bw_spv);
    VkShaderModule fp_shader = load_shader(dev, fp_spv);
    VkPipeline bw_pipe = make_pipeline(dev, bw_shader, bw_layout);
    VkPipeline fp_pipe = make_pipeline(dev, fp_shader, fp_layout);

    VkCommandPoolCreateInfo cpi = {VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    cpi.queueFamilyIndex = qf;
    VkCommandPool cmd_pool; CHECK(vkCreateCommandPool(dev, &cpi, NULL, &cmd_pool));
    VkFenceCreateInfo fci = {VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    VkFence fence; CHECK(vkCreateFence(dev, &fci, NULL, &fence));

    uint32_t bw_groups = (uint32_t)(bw_floats / 256);
    uint32_t fp_groups = fp_elems / 256;
    struct { uint32_t n; uint32_t iters; } fp_pc = {fp_elems, fp_iters};

    /* warmup */
    dispatch_time(dev, q, cmd_pool, fence, bw_pipe, bw_layout, bw_ds, bw_groups, NULL, 0);
    dispatch_time(dev, q, cmd_pool, fence, fp_pipe, fp_layout, fp_ds, fp_groups, &fp_pc, 8);

    /* BW passes */
    printf("BW_FLOATS=%" PRIu64 "\n", bw_floats);
    double bw_best = 0.0;
    for (uint32_t p = 0; p < bw_passes; p++) {
        double t = dispatch_time(dev, q, cmd_pool, fence, bw_pipe, bw_layout,
                                 bw_ds, bw_groups, NULL, 0);
        /* read+write: 2 × bw_bytes bytes */
        double gbs = 2.0 * (double)bw_bytes / t / 1e9;
        if (gbs > bw_best) bw_best = gbs;
        printf("BW_PASS pass=%u dispatch_s=%.6f gbs=%.2f\n", p, t, gbs);
    }

    /* FP passes */
    double fp_gflops_per_pass = (double)fp_elems * fp_chains * fp_iters * 2.0 / 1e9;
    printf("FP_GFLOPS_PER_PASS=%.3f\n", fp_gflops_per_pass);
    double fp_best = 0.0;
    for (uint32_t p = 0; p < fp_passes; p++) {
        double t = dispatch_time(dev, q, cmd_pool, fence, fp_pipe, fp_layout,
                                 fp_ds, fp_groups, &fp_pc, 8);
        double gflops = fp_gflops_per_pass / t;
        if (gflops > fp_best) fp_best = gflops;
        printf("FP_PASS pass=%u dispatch_s=%.6f gflops=%.1f\n", p, t, gflops);
    }

    printf("RESULT bw_peak_gbs=%.2f fp32_peak_gflops=%.1f\n", bw_best, fp_best);

    vkDestroyFence(dev, fence, NULL);
    vkDestroyCommandPool(dev, cmd_pool, NULL);
    vkDestroyPipeline(dev, bw_pipe, NULL);
    vkDestroyPipeline(dev, fp_pipe, NULL);
    vkDestroyShaderModule(dev, bw_shader, NULL);
    vkDestroyShaderModule(dev, fp_shader, NULL);
    vkDestroyDescriptorPool(dev, pool, NULL);
    vkDestroyPipelineLayout(dev, bw_layout, NULL);
    vkDestroyPipelineLayout(dev, fp_layout, NULL);
    vkDestroyDescriptorSetLayout(dev, bw_dsl, NULL);
    vkDestroyDescriptorSetLayout(dev, fp_dsl, NULL);
    vkFreeMemory(dev, bw_msrc, NULL); vkFreeMemory(dev, bw_mdst, NULL);
    vkFreeMemory(dev, fp_mem, NULL);
    vkDestroyBuffer(dev, bw_src, NULL); vkDestroyBuffer(dev, bw_dst, NULL);
    vkDestroyBuffer(dev, fp_buf, NULL);
    vkDestroyDevice(dev, NULL);
    vkDestroyInstance(inst, NULL);
    return 0;
}
C

echo "Compiling shaders..." >&2
glslangValidator -V "$TMPDIR/bw_copy.comp"  -o "$TMPDIR/bw_copy.spv"  >/dev/null
glslangValidator -V "$TMPDIR/fp32_mad.comp" -o "$TMPDIR/fp32_mad.spv" >/dev/null

echo "Compiling Vulkan driver..." >&2
gcc -std=c11 -O2 -Wall -Wextra -o "$TMPDIR/roofline" \
    "$TMPDIR/roofline.c" -lvulkan -lm

echo "Running roofline microbenchmarks..." >&2
"$TMPDIR/roofline" \
    "$TMPDIR/bw_copy.spv" "$TMPDIR/fp32_mad.spv" \
    "$BW_BUF_FLOATS" "$BW_PASSES" \
    "$FP_ELEMENTS"   "$FP_CHAINS" "$FP_ITERS" "$FP_PASSES"
