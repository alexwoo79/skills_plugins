# 用 CUPS 打印与验证

只在用户要求打印时读这份文档。`sheet.py --print` 只负责提交作业，"真的出纸"要自己验证。

## 提交与验证

```bash
lpstat -p -d -v                 # 队列是否存在、默认队列是哪个、设备 URI 是什么
lp -d QUEUE -t "标题" out.pdf   # 提交；不加 -d 用默认队列
lpstat -o                       # 还在排队的作业
tail -3 /var/log/cups/page_log  # 完成后记的页数
```

要确认页数和状态，用 IPP 查询（`ipptool` 随 CUPS 一起装）：

```bash
ipptool -tv ipp://localhost:631/jobs/<job-id> \
  /usr/share/cups/ipptool/get-job-attributes.test \
  | grep -E "job-state|impressions|media-sheets|document-format"
```

判读要点：

- `job-state = completed` + `job-media-sheets-completed = N`（N 与文档页数一致）才算真的处理了内容。
- **`total 0` / `media-sheets = 0` 表示过滤器产出了空数据**——滤镜链或 PPD 与打印机不匹配，不是"纸没出来"这么简单。
- 打印机自己的状态用厂商工具看，例如 hplip：`hp-info -d <hp:/usb/...>`；`status-code 1000 / device-state 1` 是空闲就绪，`5002` 是"设备忙/关机/未连接"。

在沙箱里 `lp`、`lpstat`、`/var/log/cups` 通常需要更高的执行权限才能访问。

## 主机型（host-based）打印机的坑

很多入门激光机（HP M12x/M13x/P110x、部分 Pantum 等）不吃 PostScript/PCL6，只接受厂商的**光栅格式**。判断方法是看 IEEE-1284 设备 ID：

```bash
lpstat -l -p QUEUE | grep -iE "connection|interface"   # 或从 error_log 里找
# HP 例子：CMD:ACL,CMD,ZJS,URF,PCLm,PJL;CID:HPLJPCLMSMV1
```

这类机型在 CUPS 里必须用厂商驱动生成的 PPD，而不是 `pxlmono` / `Generic-PDF` / 标签机 PPD 之类的通用模板。本机实测：

| 配置 | 结果 |
| --- | --- |
| DYMO 标签机 PPD（`rastertolabel`） | 作业 `total 0`，空白 |
| 通用 PCL6 PPD（`gstopxl`） | 作业 `total 0`，空白 |
| hplip `hp-laserjet_pro_mfp_m128fn.ppd`（`hpcups`，URI `hp:/usb/...`） | 3 页文档 → `media-sheets 3`，正常出纸 |

HP 机型的正确装法是 hplip：

```bash
pacman -S hplip
zcat /usr/share/ppd/HP/hp-laserjet_pro_mfp_m128fn.ppd.gz > /tmp/m128fn.ppd
lpadmin -p QUEUE -v "hp:/usb/HP_LaserJet_Pro_MFP_M128fn?serial=<SN>" -P /tmp/m128fn.ppd -E
```

`hpcups` 对该型号输出 HP 原生 PCLm：文件头是 `%PDF-1.7` + `%PCLm 1.0` + `genPCLm`，日志里能看到 `Hbpl1 ... m_szLanguage = HBPL1PAGE`。可以用它自检过滤器链路：

```bash
/usr/lib/cups/filter/hpcups <job> <user> "t" 1 "PageSize=A4 Resolution=600dpi" job.rast > out.pclm
```

（`job.rast` 可由 `pdftoraster` 生成：`/usr/lib/cups/filter/pdftoraster <job> <user> t 1 "PageSize=A4" doc.pdf > job.rast`。）

## 不要手搓打印数据流

绕开 CUPS、直接往 `/dev/usb/lp0` 或 raw 作业里灌自制的格式很容易把打印机卡在"正在打印但没有数据"的状态：

- Ghostscript 的 `-sDEVICE=pclm` 输出是 `%PDF-1.3` + `%PCLm 1.0`，这台 HP 收到后只回 `USTATUS JOB START`，0 页，且不再接受后续数据。
- `pwgtopclm`（cups-filters）需要打印机的 IPP 属性（`pclm-strip-height-preferred`、`pclm-source-resolution-supported` 等），对普通 USB 队列拿不到，会直接报 "No printer IPP attributes are supplied"。
- 打印机卡住后发 UEL（`ESC%-12345X`）可以让它结束当前作业并回到 PJL 空闲；若连写入都阻塞，就需要断电重启打印机。

结论：能装厂商驱动就装；不要再手工拼 PJL/光栅流，除非确实要逆向某种格式。

## 其他细节

- hplip 的后端用 libusb 直接接管设备，`/dev/usb/lp0` 可能因此消失（正常）。直接读该字符设备需要 root 或 `lp` 组权限。
- 旧队列改了 PPD 后记得 `lpadmin -d QUEUE` 设为默认，或者干脆删掉配错的队列，免得误选。
- 网络不可用时先确认代理：`proxyctl status` / `proxyctl tun status`（Omarchy 上由 `omarchy-proxyctl` 管理）。
