using System;
using System.Collections;
using System.Collections.Generic;
using System.Data.OleDb;
using System.Diagnostics.Eventing.Reader;
using System.Globalization;
using System.Linq;
using System.Runtime.InteropServices.Expando;
using System.Runtime.Remoting.Channels;
using System.Security.Cryptography;
using System.Threading;
using System.Web.Script.Serialization;
//using System.Windows.Forms;
using static NW.TimeSlotClass;
//using static System.Windows.Forms.AxHost;
//using static System.Windows.Forms.AxHost;

namespace NW
{
    public static class TimeSlotClass
    {
        public static List<cco_ts_info_c> cco_info = new List<cco_ts_info_c>();
        static TimeSlotClass()
        {
            for (int i = 0; i < 16; i++)
            {
                cco_ts_info_c cco_ts_info = new cco_ts_info_c();
                cco_ts_info.use = 0;
                cco_info.Add(cco_ts_info);
            }
        }

        public static void bcn_info_extract(ref beacon_pld bcn, ref csma_union_c csma_union, ref UInt32 count)
        {
            int i;

            if (bcn.路由参数条目 == null)
                return;

            for (i = 0; i < 16; i++)
            {//找到之前使用的位置
                if (cco_info[i].use == 0)
                {
                    continue;
                }
                if (cco_info[i].nid == bcn.SNID && cco_info[i].cco_mac.SequenceEqual(bcn.路由参数条目.CCO_MAC) == true)
                {
                    break;
                }
            }
            if (i >= 16)
            {//没有找到相应的队列元素，此网络未保存
                for (i = 0; i < 16; i++)
                {
                    if (cco_info[i].use == 0)
                    {
                        break;
                    }
                }
                if (i >= 16)
                {//每一个都用到了
                    int index = 0;
                    for (i = 0; i < 16; i++)
                    {
                        if (cco_info[i].rxtime < cco_info[index].rxtime)
                        {
                            index = i;
                        }
                    }
                    i = index;
                }
                
            }


            if (cco_info[i].use == 1)
            {
                if (cco_info[i].nid == bcn.SNID)
                {
                    if (cco_info[i].cco_mac.SequenceEqual(bcn.路由参数条目.CCO_MAC) == true)
                    {
                        if (cco_info[i].count == count)
                            return;
                    }
                }
            }

            cco_info[i].use = 1;
            cco_info[i].count = count;
            cco_info[i].nid = bcn.SNID;
            Array.Copy(bcn.路由参数条目.CCO_MAC, 0, cco_info[i].cco_mac, 0, 6);
            cco_info[i].rxtime = DateTime.UtcNow;
            cco_info[i].NTB = bcn.时隙分配条目.NTB;
            cco_info[i].非中央信标时隙总数 = bcn.时隙分配条目.非中央信标时隙总数;
            cco_info[i].中央信标时隙总数 = bcn.时隙分配条目.中央信标时隙总数;
            cco_info[i].CSMA相线个数 = bcn.时隙分配条目.CSMA相线个数;
            cco_info[i].信标时隙长度ms = bcn.时隙分配条目.信标时隙长度ms;
            cco_info[i].CSMA时隙大小ms = bcn.时隙分配条目.CSMA时隙大小ms;
            cco_info[i].BCSMA相线个数 = bcn.时隙分配条目.BCSMA相线个数;
            cco_info[i].TDMA时隙长度ms = bcn.时隙分配条目.TDMA时隙长度ms;
            cco_info[i].信标周期长度ms = bcn.时隙分配条目.信标周期长度ms;
   
            if (cco_info[i].CSMA相线个数 != 0)
            {
                var sortedList = csma_union.CSMA时隙.OrderBy(obj => obj.CSMA时隙长度ms).ToList();
                cco_info[i].csma_Info = sortedList;
            }

            if (cco_info[i].BCSMA相线个数 != 0)
            {
                var sortedList = csma_union.BCSMA时隙.OrderBy(obj => obj.CSMA时隙长度ms).ToList();
                cco_info[i].bcsma_Info = sortedList;
            }

            if (cco_info[i].CSMA相线个数 < 3)
            {
                cco_info[i].use = 0;
            }
        }

        public static void bcn_info_extract_gw(ref beacon_pld_gw bcn, ref csma_union_c_gw csma_union, ref UInt32 count)
        {
            int i;

            if (bcn.路由参数条目 == null)
                return;

            for (i = 0; i < 16; i++)
            {//找到之前使用的位置
                if (cco_info[i].use == 0)
                {
                    continue;
                }
                if (cco_info[i].cco_mac.SequenceEqual(bcn.CCO_MAC) == true)
                {
                    break;
                }
            }
            if (i >= 16)
            {//没有找到相应的队列元素，此网络未保存
                for (i = 0; i < 16; i++)
                {
                    if (cco_info[i].use == 0)
                    {
                        break;
                    }
                }
                if (i >= 16)
                {//每一个都用到了
                    int index = 0;
                    for (i = 0; i < 16; i++)
                    {
                        if (cco_info[i].rxtime < cco_info[index].rxtime)
                        {
                            index = i;
                        }
                    }
                    i = index;
                }

            }


            if (cco_info[i].use == 1)
            {
                //if (cco_info[i].nid == bcn.SNID)
                //{
                    if (cco_info[i].cco_mac.SequenceEqual(bcn.CCO_MAC) == true)
                    {
                        if (cco_info[i].count == count)
                            return;
                    }
                //}
            }

            cco_info[i].use = 1;
            cco_info[i].count = count;
            //cco_info[i].nid = bcn.SNID;
            Array.Copy(bcn.CCO_MAC, 0, cco_info[i].cco_mac, 0, 6);
            cco_info[i].rxtime = DateTime.UtcNow;
            cco_info[i].NTB = bcn.时隙分配条目.NTB;
            cco_info[i].非中央信标时隙总数 = bcn.时隙分配条目.非中央信标时隙总数;
            cco_info[i].中央信标时隙总数 = bcn.时隙分配条目.中央信标时隙总数;
            cco_info[i].CSMA相线个数 = bcn.时隙分配条目.CSMA相线个数;
            cco_info[i].信标时隙长度ms = bcn.时隙分配条目.信标时隙长度ms;
            cco_info[i].CSMA时隙大小ms = bcn.时隙分配条目.CSMA时隙长度ms;
            cco_info[i].BCSMA相线个数 = bcn.时隙分配条目.绑定CSMA时隙相线个数;
            cco_info[i].TDMA时隙长度ms = bcn.时隙分配条目.TDMA时隙长度ms;
            cco_info[i].信标周期长度ms = bcn.时隙分配条目.信标周期长度ms;

            if (cco_info[i].CSMA相线个数 != 0)
            {
                var sortedList = csma_union.CSMA时隙.OrderBy(obj => obj.CSMA时隙长度ms).ToList();
                cco_info[i].csma_Info1 = sortedList;
                cco_info[i].csma_Info = sortedList.Select(obj => new csma_info_c
                {
                    CSMA时隙长度ms = obj.CSMA时隙长度ms,
                    CSMA时隙相线 = obj.CSMA时隙相线
                }).ToList();
            }

            if (cco_info[i].BCSMA相线个数 != 0)
            {
                var sortedList = csma_union.BCSMA时隙.OrderBy(obj => obj.CSMA时隙长度ms).ToList();
                cco_info[i].bcsma_Info1 = sortedList;
                cco_info[i].bcsma_Info = sortedList.Select(obj => new csma_info_c
                {
                    CSMA时隙长度ms = obj.CSMA时隙长度ms,
                    CSMA时隙相线 = obj.CSMA时隙相线
                }).ToList();
            }

            if (cco_info[i].CSMA相线个数 < 3)
            {
                cco_info[i].use = 0;
            }
        }

        public static string bcn_slot_cal(ref byte nid, ref UInt32 rxtime)
        {
            string bcn_slot_type = "";
            int i = 0;
            for (i = 0; i < 16; i++)
            {
                if (cco_info[i].use == 0)
                {
                    continue;
                }
                if (cco_info[i].nid == nid)
                {
                    break;
                }
            }
            if (i >= 16)
            {
                return bcn_slot_type;
            }


            UInt32 tmp32 = (rxtime - cco_info[i].NTB) / 100 * 4; //差值转换成us
            UInt32 total = (UInt32)(cco_info[i].非中央信标时隙总数 + cco_info[i].中央信标时隙总数) * ((UInt32)cco_info[i].信标时隙长度ms * 1000);

            if (tmp32 > total)
            {
                bcn_slot_type = "CSMA";
                return bcn_slot_type;
            }
            bcn_slot_type = "BT" + (UInt16)(tmp32 / (cco_info[i].信标时隙长度ms * 1000));
            return bcn_slot_type;
        }


        public static string sof_slot_cal(ref byte nid, ref UInt32 rxtime, ref string  debug)
        {
            string slot_type = "";
            int i = 0;
            UInt32 tmp32 = 0;
            UInt32 start_ntb = 0;     //us
            UInt32 current_ntb = rxtime;
            UInt32 bcn_time = 0;
            UInt32 tdma_time = 0;
            for (i = 0; i < 16; i++)
            {
                if (cco_info[i].use == 0)
                {
                    continue;
                }
                if (cco_info[i].nid == nid)
                {
                    start_ntb = cco_info[i].NTB;
                    tmp32 = (current_ntb - start_ntb) / 100 * 4;
                    if (tmp32 < (cco_info[i].信标周期长度ms * 1000))
                    {//在信标周期内
                        break;
                    }
                    cco_info[i].use = 0;
                }
            }
            if (i >= 16)
            {
                //slot_type = "no_nid";
                //debug += "current_ntb:" + current_ntb;
                //for (i = 0; i < 16; i++)
                //{
                //    if (cco_info[i].use == 0)
                //    {
                //        continue;
                //    }
                //    debug += "|nid:" + cco_info[i].nid;
                //    debug += "|start_ntb:" + start_ntb;
                //    debug += "|信标周期长度ms:" + cco_info[i].信标周期长度ms;
                //}
                return slot_type;
            }


            bcn_time = (UInt32)(cco_info[i].信标时隙长度ms * 1000 * ((UInt32)cco_info[i].非中央信标时隙总数 + (UInt32)cco_info[i].中央信标时隙总数));
            if (tmp32 < bcn_time)
            {//信标时间内
                slot_type = "BT" + tmp32 / (cco_info[i].信标时隙长度ms * 1000);
                return slot_type;
            }

            start_ntb += bcn_time * 100 / 4;
            if (cco_info[i].CSMA相线个数 != 0)
            {
                string phase = "";
                if (csma_get(ref cco_info[i].csma_Info, ref phase, ref cco_info[i].CSMA时隙大小ms, ref start_ntb, current_ntb)== 1)
                {
                    slot_type += "CSMA-" + phase;
                    return slot_type;
                }
            }

            if (cco_info[i].TDMA时隙长度ms != 0)
            {
                tdma_time = (UInt32)(cco_info[i].TDMA时隙长度ms * 1000 * ((UInt32)cco_info[i].非中央信标时隙总数 + (UInt32)cco_info[i].中央信标时隙总数));
                tmp32 = (current_ntb - start_ntb) / 100 * 4;
                if (tmp32 < tdma_time)
                {//信标时间内
                    slot_type = "TDMA" + tmp32 / (cco_info[i].TDMA时隙长度ms * 1000);
                    return slot_type;
                }
                start_ntb += tdma_time * 100 /4 ;
            }

            if (cco_info[i].BCSMA相线个数 != 0)
            {
                string phase = "";
                if (csma_get(ref cco_info[i].bcsma_Info, ref phase, ref cco_info[i].CSMA时隙大小ms, ref start_ntb, current_ntb) == 1)
                {
                    slot_type = "BIND-" + phase;
                    return slot_type;
                }
            }
            return slot_type;
        }


        public static int csma_get(ref List<csma_info_c> csma_Info, ref string phase, ref UInt16 csma_lenms, ref UInt32 start_ntb, UInt32 current_ntb)
        {
            UInt32 tmp32;
            int k;
            byte[] index = new byte[3] { 0, 0, 0 };
            UInt32[] csma_slice = new UInt32[3];
            UInt32[] csma_time = new UInt32[3];
            UInt32 csma_slice_total = 0;
            UInt32 csma_slice_len = 1000 * (UInt32)csma_lenms;

            for (int j = 0; j < 3; j++)
            {
                csma_time[j] = (UInt32)(csma_Info[j].CSMA时隙长度ms * 1000);
            }

            for (int j = 0; j < 3; j++)
            {
                csma_slice[j] = (csma_time[j] / csma_slice_len) == 0 ? 1 : (csma_time[j] / csma_slice_len);
            }

            csma_slice_total = csma_slice[0] + csma_slice[1] + csma_slice[2];
            UInt32[,] csma_slice_info = new UInt32[csma_slice_total, 2];
            int m = 0;
            for (k = 0; k < 2; k++)
            {
                for (int j = 0; j < csma_slice[k]; j++) //第一个相线
                {
                    m = (int) (k+ csma_slice_total * j / csma_slice[k]);
                    while (csma_slice_info[m, 0] != 0)
                    {//若对应位置已经存放时隙片，则往后寻找一个空位
                        m++;
                    }
                    if (m >= csma_slice_total) //满足此条件证明出现了错误，直接返回
                        return 0;

                    if (j == (csma_slice[k] - 1))
                    {//最后一片
                        csma_slice_info[m, 0] = csma_time[k];
                    }
                    else
                    {
                        csma_slice_info[m, 0] = csma_slice_len;
                        csma_time[k] -= csma_slice_len;
                    }
                    csma_slice_info[m, 1] = csma_Info[k].CSMA时隙相线;
                }
            }

            for ( k = 0; k < csma_slice_total; k++)
            {
                if (csma_slice_info[k, 0] == 0)
                {
                    if (k == csma_slice_total - 1)
                    {
                        csma_slice_info[k, 0] = csma_time[2];
                    }
                    else
                    {
                        csma_slice_info[k, 0] = csma_slice_len;
                        csma_time[2] -= csma_slice_len;
                    }
                    csma_slice_info[k, 1] = csma_Info[2].CSMA时隙相线;
                }
            }

            for ( k = 0; k < csma_slice_total; k++)
            {
                if (current_ntb == start_ntb)
                    break;

                else if (current_ntb > start_ntb)
                {
                    tmp32 = start_ntb + csma_slice_info[k, 0] * 100 / 4;
                    if (tmp32 <= current_ntb)
                    {
                        start_ntb = tmp32;
                    }
                    else
                    {
                        break;
                    }
                }
                else
                {
                    tmp32 = start_ntb + csma_slice_info[k, 0] * 100 / 4;
                    if (tmp32 <= current_ntb)
                    {
                        start_ntb = tmp32;
                    }
                    else
                    {
                        if ((tmp32 - current_ntb) > csma_slice_info[k, 0] * 100 / 4)
                        {
                            start_ntb = tmp32;
                        }
                        else
                        {
                            break;
                        }
                    }
                }
            }

            if (k < csma_slice_total)
            {
                if (csma_slice_info[k, 1] == 1)
                {
                    phase = "A";
                }
                else if (csma_slice_info[k, 1] == 2)
                {
                    phase = "B";
                }
                else if (csma_slice_info[k, 1] == 3)
                {
                    phase = "C";
                }
                else
                {
                    phase = "    " + csma_slice_info[k, 1].ToString("X2");
                }
                return 1;
            }


            return 0;
        }
    }

    public class cco_ts_info_c
    {
        public byte use;                //使用标识 0未使用
        public UInt32 count;            //信标周期计数
        public byte nid;
        public byte[] cco_mac = new byte[6];
        public DateTime rxtime;           //最新的信标接收时间
        public UInt32 NTB;              /*!< 信标周期起始网络基准时间，信标周期的开始时刻的 NTB值 */
        public byte 非中央信标时隙总数; /*!< 非中央信标时隙总数 */
        public byte 中央信标时隙总数;   /*!< 中央信标时隙总数 */
        public byte CSMA相线个数;       /*!< CSMA时隙支持的相线个数 */
        //public byte 代理信标时隙总数;   /*!< 代理信标时隙总数 */
        public double 信标时隙长度ms;/*!< 信标时隙长度100us，每个信标时隙占用的时隙长度，单位: 100us */
        public UInt16 CSMA时隙大小ms;   /*!< CSMA时隙大小10ms，CSMA时隙分片大小，单位: 10ms */
        public byte BCSMA相线个数;      /*!< 绑定CSMA时隙相线个数，取值范围: 0~3 */
        public double  TDMA时隙长度ms;/*!< TDMA时隙长度100us，单位: 100us */       
        public double 信标周期长度ms;   /*!< 信标周期长度100us，信标周期的时间长度，单位: 100us */
        public List<csma_info_c> csma_Info; //csma时隙分配
        public List<csma_info_c> bcsma_Info;//bcsma时隙分配

        public List<csma_info_c_gw> csma_Info1; //国网csma时隙分配
        public List<csma_info_c_gw> bcsma_Info1;//国网bcsma时隙分配
    }
        

    /// <summary>
    /// 信标帧控制
    /// </summary>
    public class ctrl_beacon
    {
        public string 原始数据;
        public byte 定界符类型;
        public byte 接入指示;
        public byte SNID;
        public string 信标时间戳;
        public UInt32 信标周期计数;          /* beacon period count */
        public string 源TEI;
        public UInt16 TMI;
        public UInt16 符号数;
        public byte rsvd;
        public string 相线;
        public byte 标准版本号;
        public string 帧控制校验;
    }

    /// <summary>
    /// 国网载波信标帧控制
    /// </summary>
    public class ctrl_beacon_gw
    {
        public string 原始数据;
        public string 定界符类型;
        public byte 网络类型;
        public string NID;
        public string 信标时间戳;
        public string 源TEI;
        public UInt16 分集拷贝基本模式;
        public UInt32 符号数;
        public string 相线;
        public UInt16 rsvd;
        public byte 标准版本号;
        public string 帧控制校验;
    }

    /// <summary>
    /// 国网无线信标帧控制
    /// </summary>
    public class ctrl_beacon_rf_gw
    {
        public string 原始数据;
        public string 定界符类型;
        public byte 网络类型;
        public string NID;
        public string 信标时间戳;
        public string 源TEI;
        public string MCS;
        public UInt16 载荷PB大小;
        public string rsvd;
        public byte 标准版本号;
        public string 帧控制校验;
    }

    /// <summary>
    /// 无线信标帧控制
    /// </summary>
    public class ctrl_beacon_rf
    {
        public string 原始数据;
        public byte 定界符类型;
        public byte 接入指示;
        public byte SNID;
        public string 信标时间戳;
        public UInt32 信标周期计数;          /* beacon period count */
        public string 源TEI;
        public string MCS;
        public UInt16 载荷PB大小;
        public string resv;
        public byte 标准版本号;
        public string 帧控制校验;
    }

    /// <summary>
    /// SOF 帧控制
    /// </summary>
    public class ctrl_sof
    {
        public string 原始数据;
        public byte 定界符类型;
        public byte 接入指示;
        public byte SNID;
        public string 源TEI;
        public string 目的TEI;
        public byte 链路标识符;
        public UInt16 rsvd;
        public byte 物理块个数;
        public byte TMI;
        public UInt16 帧长10us;    /* unit: 10us */
        public UInt16 rsvd2;
        public byte TEI过滤标志位; /* is phy check TEI, fix to 1 at this 标准版本号 */
        public byte 重传标志位;
        public UInt16 符号数;
        public byte TMI_EXT;
        public byte 标准版本号;
        public string 帧控制校验;
    };

    /// <summary>
    /// 国网载波SOF帧控制
    /// </summary>
    public class ctrl_sof_gw
    {
        public string 原始数据;
        public string 定界符类型;
        public byte 网络类型;
        public string NID;
        public string 源TEI;
        public string 目的TEI;
        public byte 链路标识符;
        public UInt16 帧长;
        public UInt16 物理块个数;
        public UInt16 符号数;
        public UInt16 广播标志位;
        public UInt16 重传标志位;
        public UInt16 加密标志位;
        public UInt16 分集拷贝基本模式;
        public byte 分集拷贝扩展模式;
        public byte 标准版本号;
        public string 帧控制校验;
    }

    /// <summary>
    /// 国网无线SOF帧控制
    /// </summary>
    public class ctrl_sof_rf_gw
    {
        public string 原始数据;
        public string 定界符类型;
        public byte 网络类型;
        public string NID;
        public string 源TEI;
        public string 目的TEI;
        public UInt32 链路标识符;
        public UInt16 帧长;
        public UInt16 载荷PB大小;
        public UInt16 resv1;
        public UInt16 广播标志位;
        public UInt16 重传标志位;
        public UInt16 加密标志位;
        public UInt16 MCS;
        public byte resv2;
        public byte 标准版本号;
        public string 帧控制校验;
    }


    /// <summary>
    /// SOF 帧控制RF
    /// </summary>
    public class ctrl_sof_rf
    {
        public string 原始数据;
        public byte 定界符类型;
        public byte 接入指示;
        public byte SNID;
        public string 源TEI;
        public string 目的TEI;
        public byte 链路标识符;
        public UInt16 帧长100us;    /* unit: 100us */
        public UInt16 载荷PB大小;
        public string MCS;
        public byte TEI过滤标志位; /* is phy check TEI, fix to 1 at this 标准版本号 */
        public byte 重传标志位; 
        public byte resv1;
        public UInt32 resv2;
        public byte resv3;
        public byte 标准版本号;
        public string 帧控制校验;
    };

    /// <summary>
    /// 选择确认帧
    /// </summary>
    public class ctrl_sack
    {
        public string 原始数据;
        public byte 定界符类型;
        public byte 接入指示;
        public byte SNID;
        public sack_c ACK;
        public sack_rf_c ACK_RF;
        public search_c 网络搜索帧;
        public sync_c 同步帧;
        public slot_c 时隙预约帧;
        public byte 扩展帧类型;
        public byte 标准版本号;
        public UInt32 帧控制校验;
    };

    /// <summary>
    /// 国网载波选择确认帧
    /// </summary>
    public class ctrl_sack_gw
    {
        public string 原始数据;
        public string 定界符类型;
        public byte 网络类型;
        public string NID;
        public byte 接收结果;
        public byte 接收状态;
        public string 源TEI;
        public string 目的TEI;
        public byte 接收物理块个数;
        public byte resv1;
        public UInt16 信道质量;
        public UInt16 站点负载;
        public byte resv2;
        public byte 扩展帧类型;
        public byte 标准版本号;
        public string 帧控制校验;
    };

    /// <summary>
    /// 国网无线选择确认帧
    /// </summary>
    public class ctrl_sack_rf_gw
    {
        public string 原始数据;
        public string 定界符类型;
        public byte 网络类型;
        public string NID;
        public byte 接收结果;
        public byte resv1;
        public string 源TEI;
        public string 目的TEI;
        public byte resv2;
        public UInt16 信道质量;
        public UInt16 站点负载;
        public byte resv3;
        public byte 扩展帧类型;
        public byte 标准版本号;
        public string 帧控制校验;
    };

    //ACK帧rf
    public class sack_rf_c
    {
        public byte 接收结果;
        public byte resv1;
        public string 目的TEI;
        public byte resv2;
        public byte[] resv3 = new byte[8];
    }

    //ACK帧
    public class sack_c
    {
        public byte 接收结果;
        public byte 接收状态;
        public string 目的TEI;
        public byte 接收物理块个数;
        public byte[] rsvd = new byte[8];
    }

    public class search_c
    {
        public byte[] 目标站点地址 = new byte[6];
        public string 源TEI;
        public UInt32 resv;
        public byte 序号;
    }

    public class sync_c
    {
        public UInt32 时间戳;
        public string 源TEI;
        public byte resv;
        public UInt32 resv2;
        public byte 序号;
    }

    public class slot_c
    {
        public string 源TEI;
        public string 目的TEI;
        public string 预约类型;
        public byte 层级;
        public string 确认补发tei;
        public byte 接收PB块个数;
        public byte 接收结果;
        public byte 报文索引;
        public byte[] rsvd = new byte[4];
    }

    /// <summary>
    /// cco网间协调帧
    /// </summary>
    public class ctrl_coord
    {
        public string 原始数据;
        public byte 定界符类型;
        public byte 接入指示;
        public byte SNID;
        public string 邻居网络;
        public byte 本网络无线信道编号;
        public UInt16 rsvd;
        public UInt32 持续时间ms;
        public byte rsvd2;
        public string 带宽结束标识;
        public byte 本网络无线option;
        public byte rsvd3;
        public UInt16 带宽结束偏移ms;
        public UInt16 带宽开始偏移ms;
        public byte rsvd4;
        public byte 标准版本号;
        public string 帧控制校验;
    };

    /// <summary>
    /// cco国网载波网间协调帧
    /// </summary>
    public class ctrl_coord_gw
    {
        public string 原始数据;
        public string 定界符类型;
        public byte 网络类型;
        public string NID;
        public UInt32 持续时间ms;
        public UInt16 带宽开始偏移ms;
        public UInt32 接收到的邻居网络号;
        public byte 本网络无线信道编号;
        public byte rsvd;
        public byte 标准版本号;
        public string 帧控制校验;
    };

    /// <summary>
    /// 信标帧载荷
    /// </summary>
    public class beacon_pld
    {
        public string 信标类型;           /*!< 信标类型，0: 发现信标; 1: 代理信标; 2:中央信标 */
        public string 组网标志位;       /*!< 组网标志位，0: 组网未完成; 1: 组网完成 */
#if NWSM
        public byte 精简信标标志;
#else
        public byte resv1;              /*!< 保留 */
#endif
        public string 多网络优选功能开关; /*!< 多网络优选功能开关(multi-network selection flag) 0: 未使能网络评估; 1: 使能网络评估 */
        public string 开始关联标志位;     /*!< 开始关联标志位，0: 不允许站点发起关联请求, 1: 允许站点发起关联请求 */
        public byte resv2;              /*!< 保留 */
        public byte 组网序列号;         /*!< 组网序列号，递增，CCO每次启动都需要 +1 */
        public byte SNID;               /*!< 短网络标识[1~15] */
#if NWSM
        public byte 本网络无线option;
        public byte resv3;              /*!< 保留 */
        public byte 本网络无线信道编号;
        public UInt16 resv4;            /*!< 保留 */
#else
        public byte resv3;              /*!< 保留 */
        public UInt32 resv4;            /*!< 保留 */
#endif
        public byte[] 信标管理信息;            /*!< 信标管理信息(beacon managment information) */
        public byte 信标条目数;         /*!< 信标条目数(beacon items numbers) */
        public bitem_sta_cap_c 站点能力条目;   
        public bitem_ts_c 时隙分配条目;        
        public bitem_rt_params_c 路由参数条目; 
        public bitem_chg_band_c 频段变更条目;
        public bitem_rfrt_params_c 无线路由参数条目;
        public bitem_band_detect_c 频段探测条目;
        public bitem_rf_chnl_chg_c 无线信道变更条目;
        public bitem_calendar_c 万年历条目;
        public bitem_zuslot_c 组时隙分配目;
        public bitem_mincltcfg_c 分钟采集配置条目;
        public bitem_yxd_private_c 友讯达设置私有参数条目;

        public string CRC32;        /*!< CRC32 */
    }

    /// <summary>
    /// 国网标准信标帧载荷
    /// </summary>
    public class beacon_pld_gw
    {
        public string 信标类型;           /*!< 信标类型，0: 发现信标; 1: 代理信标; 2:中央信标 */
        public string 组网标志位;       /*!< 组网标志位，0: 组网未完成; 1: 组网完成 */
        public string 精简信标标志;
        public byte resv1;              /*!< 保留 */
       // public string 多网络优选功能开关; /*!< 多网络优选功能开关(multi-network selection flag) 0: 未使能网络评估; 1: 使能网络评估 */
        public string 开始关联标志位;     /*!< 开始关联标志位，0: 不允许站点发起关联请求, 1: 允许站点发起关联请求 */
        public string 信标使用标志位;
        //public byte resv2;              /*!< 保留 */
        public byte 组网序列号;         /*!< 组网序列号，递增，CCO每次启动都需要 +1 */
        //public byte SNID;               /*!< 短网络标识[1~15] */
        //public UInt64 CCOMAC地址;
        public byte[] CCO_MAC = new byte[6];
        public UInt32 信标周期计数;
        public byte 本网络无线信道编号;
        //public byte 本网络无线option;
        public byte[] resv3 = new byte[7];              /*!< 保留 */

        public byte[] 信标管理信息;            /*!< 信标管理信息(beacon managment information) */
        public byte 信标条目数;         /*!< 信标条目数(beacon items numbers) */
        public bitem_sta_cap_c_gw 站点能力条目;
        public bitem_rt_params_c_gw 路由参数条目;
        public bitem_ts_c_gw 时隙分配条目;
        public bitem_chg_band_c_gw 频段变更条目;
        public bitem_rfrt_params_c_gw 无线路由参数条目;
       // public bitem_band_detect_c 频段探测条目;
        public bitem_rf_chnl_chg_c_gw 无线信道变更条目;
        //public bitem_calendar_c 万年历条目;
       // public bitem_zuslot_c 组时隙分配目;
       // public bitem_mincltcfg_c 分钟采集配置条目;
        public bitem_yxd_private_c 友讯达设置私有参数条目;

        public string CRC32;        /*!< 帧载荷校验序列CRC32 */
    }

    /// <summary>
    /// 精简信标帧载荷
    /// </summary>
    public class beacon_pld_jj
    {
        public string 信标类型;           /*!< 信标类型，0: 发现信标; 1: 代理信标; 2:中央信标 */
        public string 组网标志位;       /*!< 组网标志位，0: 组网未完成; 1: 组网完成 */
        public byte 精简信标标志;
        public byte resv1;              /*!< 保留 */
        public string 开始关联标志位;     /*!< 开始关联标志位，0: 不允许站点发起关联请求, 1: 允许站点发起关联请求 */
        public string 信标使用标志位;     /*!< 信标使用标志位，0: 不使用, 1: 使用 */
        public byte 组网序列号;         /*!< 组网序列号，递增，CCO每次启动都需要 +1 */
        public byte[] CCO_MAC = new byte[6];
        public UInt32 信标周期计数;
        public byte[] binfo;            /*!< 信标管理信息(beacon managment information) */
        public byte 信标条目数;         /*!< 信标条目数(beacon items numbers) */
        public bitem_sta_cap_and_slot_c 站点能力及时隙条目;
        public string CRC32;        /*!< CRC32 */
    }


    /// <summary>
    /// 国网精简信标帧载荷
    /// </summary>
    public class beacon_pld_jj_gw
    {
        public string 信标类型;           /*!< 信标类型，0: 发现信标; 1: 代理信标; 2:中央信标 */
        public string 组网标志位;       /*!< 组网标志位，0: 组网未完成; 1: 组网完成 */
        public string 精简信标标志;
        public byte resv1;              /*!< 保留 */
        public string 开始关联标志位;     /*!< 开始关联标志位，0: 不允许站点发起关联请求, 1: 允许站点发起关联请求 */
        public string 信标使用标志位;     /*!< 信标使用标志位，0: 不使用, 1: 使用 */
        public byte 组网序列号;         /*!< 组网序列号，递增，CCO每次启动都需要 +1 */
        public byte[] CCO_MAC = new byte[6];
        public UInt32 信标周期计数;
        public byte[] binfo;            /*!< 信标管理信息(beacon managment information) */
        public byte 信标条目数;         /*!< 信标条目数(beacon items numbers) */
        public bitem_sta_cap_and_slot_c_gw 站点能力及时隙条目;
        public string CRC32;        /*!< CRC32 */
    }

    /**
    * 信标条目: 站点能力以及时隙条目(0x0E)
    */
    public class bitem_sta_cap_and_slot_c
    {
        public byte[] 原始数据;
        public byte 条目头;                    /*!< 条目头，站点能力条目: 0x01 */
        public byte 条目长度;                  /*!< 条目长度，单位: 字节，固定取 0x16 */
        
        public string TEI;                     // :12 /*!< 站点 TEI */
        public string PCO;                     // :12 /*!< 代理站点 TEI */
        public UInt16 角色;                    // :4  /*!< 角色 */
        public byte 层级;                      // :6  /*!< 层级数，不大于 15 */
        public byte[] 发送信标站点MAC地址 = new byte[6];       /*!< 发送信标站点的MAC地址 */
        public byte 链路上RF跳数;
        public byte 载波频段;
        public byte resv;
        public string CSMA时隙开始时间;        /*!< csma 时隙开始时间，单位NTB */
        public UInt32 CSMA时隙长度;            /*!< csma 时隙持续时间，单位1ms */
    }

    /**
    * 信标条目: 国网站点能力以及时隙条目(0x0E)
    */
    public class bitem_sta_cap_and_slot_c_gw
    {
        public byte[] 原始数据;
        public byte 条目头;                    /*!< 条目头，站点能力条目: 0x01 */
        public byte 条目长度;                  /*!< 条目长度，单位: 字节，固定取 0x16 */

        public string TEI;                     // :12 /*!< 站点 TEI */
        public string PCO;                     // :12 /*!< 代理站点 TEI */
        public UInt16 角色;                    // :4  /*!< 角色 */
        public byte 层级;                      // :6  /*!< 层级数，不大于 15 */
        public byte[] 发送信标站点MAC地址 = new byte[6];       /*!< 发送信标站点的MAC地址 */
        public byte 链路上RF跳数;
        //public byte 载波频段;
        public byte resv;
        public string CSMA时隙开始时间;        /*!< csma 时隙开始时间，单位NTB */
        public UInt32 CSMA时隙长度;            /*!< csma 时隙持续时间，单位1ms */
    }


    /**
    * 信标条目: 站点能力条目(0x01)，参考文档 表33
    */
    public class bitem_sta_cap_c
    {
        public byte[] 原始数据;
        public byte 条目头;                    /*!< 条目头，站点能力条目: 0x01 */
        public byte 条目长度;                  /*!< 条目长度，单位: 字节，固定取 0x16 */
        public byte 层级;                      // :6  /*!< 层级数，不大于 15 */
        public string 相线;                      // :2  /*!< 相线，站点所属相线，0:全相线; 1:A相; 2:B相; 3:C相 */
        public string TEI;                     // :12 /*!< 站点 TEI */
        public UInt16 角色;                    // :4  /*!< 角色 */
        public string 信标使用标志位;            /*!< 信标使用标志位，表示是否使用信标评估信道，0:不使用 */
        public byte[] 发送信标站点MAC地址 = new byte[6];       /*!< 发送信标站点的MAC地址 */
        public string 代理节点TEI;             // :12 /*!< 代理站点 TEI，表示发送信标站点的代理站点 TEI */
#if NWSM
        public byte 链路上RF跳数;              // :4  /*!< 链路上RF跳数 */
#else
        public UInt16 resv1;                   // :4  /*!< 保留 */
#endif
        public UInt32 最低成功率;              /*!< 路径最低通信成功率，表示该站点到CCO的整个路径的最小通信成功率 CCO时取值 100% */
        public UInt32 resv2;                   /*!< 保留 */
    }

    /**
    * 信标条目: 站点能力条目(0x01)，参考文档 表33
    */
    public class bitem_sta_cap_c_gw
    {
        public byte[] 原始数据;
        public byte 条目头;                    /*!< 条目头，站点能力条目: 0x01 */
        public byte 条目长度;                  /*!< 条目长度，单位: 字节，固定取 0x16 */
        public string TEI;                     // :12 /*!< 站点 TEI */
        public string 代理站点TEI;
        public UInt32 最低成功率;              /*!< 路径最低通信成功率，表示该站点到CCO的整个路径的最小通信成功率 CCO时取值 100% */
        public byte[] 发送信标站点MAC地址 = new byte[6];       /*!< 发送信标站点的MAC地址 */
        public string 角色;                    // :4  /*!< 角色 */
        public byte 层级数;                      // :6  /*!< 层级数，不大于 15 */
        public UInt16 代理站点信道质量;
        public string 相线;                      // :2  /*!< 相线，站点所属相线，0:全相线; 1:A相; 2:B相; 3:C相 */
        public byte 链路上RF跳数;              // :4  /*!< 链路上RF跳数 */
        public byte resv1;
        // public string 信标使用标志位;            /*!< 信标使用标志位，表示是否使用信标评估信道，0:不使用 */

    }

    /**
    * 信标条目: 时隙分配条目(0x02)，参考文档 表34
    */
    public class bitem_ts_c
    {
        public byte[] 数据;
        public byte 条目头;             /*!< 条目头，时隙分配条目: 0x02 */
        public UInt16 条目长度;         /*!< 条目长度，单位: 字节，范围: 3~512 */
        public byte 非中央信标时隙总数; /*!< 非中央信标时隙总数 */
        public byte 中央信标时隙总数;   /*!< 中央信标时隙总数 */
        public byte CSMA相线个数;       /*!< CSMA时隙支持的相线个数 */
        public byte 代理信标时隙总数;   /*!< 代理信标时隙总数 */
        public double 信标时隙长度ms;/*!< 信标时隙长度100us，每个信标时隙占用的时隙长度，单位: 100us */
        public UInt16 CSMA时隙大小ms;   /*!< CSMA时隙大小10ms，CSMA时隙分片大小，单位: 10ms */
        public byte BCSMA相线个数;      /*!< 绑定CSMA时隙相线个数，取值范围: 0~3 */
        public byte BCSMA_lid;          /*!< 绑定CSMA时隙链路标识符，绑定CSMA时隙支持的业务报文LID */
        public double TDMA时隙长度ms;/*!< TDMA时隙长度100us，单位: 100us */
        public byte TDMA_LID;           /*!< TDMA时隙时隙链路标识符， TDMA时隙支持的业务报文LID */
        public UInt32 NTB;              /*!< 信标周期起始网络基准时间，信标周期的开始时刻的 NTB值 */
        public double 信标周期长度ms;    /*!< 信标周期长度100us，信标周期的时间长度，单位: ms */
#if NWSM
        public UInt16 RF信标时隙长度ms;    /*!< bit 0-10,RF 链路上信标时隙长度单位：1 毫秒 */
#endif
        public UInt32 resv;             /*!< 保留 */
        public byte[] 非中央信标时隙信息字段;         /*!< 非中央信标时隙信息 */
        //public List<ncb_info_c> 非中央信标;
        public List<string> 非中央信标;
        public byte[] CSMA时隙信息字段;        /*!< CSMA时隙信息 */
        public List<string> CSMA时隙;
        public byte[] BCSMA时隙信息字段;       /*!< 绑定CSMA标时隙信息 */
        public List<string> BCSMA时隙;
    }

    /**
   * 信标条目: 国网时隙分配条目(0x02)，参考文档 表34
   */
    public class bitem_ts_c_gw
    {
        public byte[] 数据;
        public byte 条目头;             /*!< 条目头，时隙分配条目: 0x02 */
        public UInt16 条目长度;         /*!< 条目长度，单位: 字节，范围: 3~512 */
        public byte 非中央信标时隙总数; /*!< 非中央信标时隙总数 */
        public byte 中央信标时隙总数;   /*!< 中央信标时隙总数 */
        public byte CSMA相线个数;       /*!< CSMA时隙支持的相线个数 */
        public UInt16 resv1;
        public byte 代理信标时隙总数;   /*!< 代理信标时隙总数 */
        public double 信标时隙长度ms;/*!< 信标时隙长度100us，每个信标时隙占用的时隙长度，单位: 100us */
        public UInt16 CSMA时隙长度ms;   /*!< CSMA时隙大小10ms，CSMA时隙分片大小，单位: 10ms */
        public byte 绑定CSMA时隙相线个数;      /*!< 绑定CSMA时隙相线个数，取值范围: 0~3 */
        public byte 绑定CSMA时隙链路标识符;          /*!< 绑定CSMA时隙链路标识符，绑定CSMA时隙支持的业务报文LID */
        public double TDMA时隙长度ms;/*!< TDMA时隙长度100us，单位: 100us */
        public byte TDMA时隙链路标识符;           /*!< TDMA时隙时隙链路标识符， TDMA时隙支持的业务报文LID */
        public UInt32 NTB;              /*!< 信标周期起始网络基准时间，信标周期的开始时刻的 NTB值 */
        public double 信标周期长度ms;    /*!< 信标周期长度100us，信标周期的时间长度，单位: ms */

        public UInt16 RF信标时隙长度ms;    /*!< bit 0-10,RF 链路上信标时隙长度单位：1 毫秒 */

        public UInt16 resv2;             /*!< 保留 */
        public byte[] 非中央信标时隙信息字段;         /*!< 非中央信标时隙信息 */
        //public List<ncb_info_c> 非中央信标;
        public List<string> 非中央信标;
        public byte[] CSMA时隙信息字段;        /*!< CSMA时隙信息 */
        public List<string> CSMA时隙;
        public byte[] BCSMA时隙信息字段;       /*!< 绑定CSMA标时隙信息 */
        public List<string> BCSMA时隙;
    }

    public class csma_union_c
    {
        public List<ncb_info_c> 非中央信标;
        public List<csma_info_c> CSMA时隙;
        public List<csma_info_c> BCSMA时隙;
    }


    public class csma_union_c_gw
    {
        public List<ncb_info_c_gw> 非中央信标;
        public List<csma_info_c_gw> CSMA时隙;
        public List<csma_info_c_gw> BCSMA时隙;
    }

    /**
     * 非中央信标信息字段，参考文档 表35
     */
    public class ncb_info_c
    {
        public string TEI;   //:12/*!< 站点 TEI */
        public string 信标类型; //:1 /*!< 信标类型，0: 发现信标; 1: 代理信标 */
        public UInt16 resv;  //:3 /*!< 保留 */
    }

    /**
     * 国网非中央信标信息字段，参考文档 表35
     */
    public class ncb_info_c_gw
    {
        public string TEI;   //:12/*!< 站点 TEI */
        public string 信标类型; //:1 /*!< 信标类型，0: 发现信标; 1: 代理信标 */
        public string 无线信标标志;  
    }

    /**
     * CSMA时隙信息字段，参考文档 表36
     */
    public class csma_info_c
    {
        public double CSMA时隙长度ms;  //:24/*!< CSMA时隙长度，单位: 100us */
        public UInt32 CSMA时隙相线;       //:8 /*!< CSMA时隙相线 */
    }

    /**
     * 国网CSMA时隙信息字段，参考文档 表36
     */
    public class csma_info_c_gw
    {
        public double CSMA时隙长度ms;  //:24/*!< CSMA时隙长度，单位: 100us */
        public UInt32 CSMA时隙相线;       //:8 /*!< CSMA时隙相线 */
        public byte resv;//保留
    }

    /**
    * 信标条目: 路由参数条目(0x06)，参考文档 表38
    */
    public class bitem_rt_params_c
    {
        public byte[] 数据;
        public byte 条目头;           /*!< 条目头，路由参数通知条目: 0x06 */
        public UInt16 条目长度;         /*!< 条目长度，单位: 字节，固定取: 0x22 */
        public UInt16 路由周期s;        /*!< 路由周期，单位: 秒 */
        public UInt16 resv1;         /*!< 保留 */
        public UInt16 路由评估剩余时间s;        /*!< 路由评估剩余时间，单位: 秒 */
        public byte[] resv2 = new byte[20];     /*!< 保留 */
        public byte[] CCO_MAC = new byte[6];    /*!< CCO MAC地址 */
    }

    /**
    * 信标条目: 国网路由参数条目(0x06)，参考文档 表38
    */
    public class bitem_rt_params_c_gw
    {
        public byte[] 数据;
        public byte 条目头;           /*!< 条目头，路由参数通知条目: 0x06 */
        public UInt16 条目长度;         /*!< 条目长度，单位: 字节，固定取: 0x22 */
        public UInt16 路由周期s;        /*!< 路由周期，单位: 秒 */
        public UInt16 路由评估剩余时间s;        /*!< 路由评估剩余时间，单位: 秒 */
        public UInt16 代理站点发现列表周期s;         /*!< 保留 */
        public UInt16 发现站点发现列表周期s;       
   
    }

    /**
     * 信标条目: 频段变更条目(0x07)，参考文档 表39
     */
    public class bitem_chg_band_c
    {
        public byte[] 数据;
        public byte 条目头;                    /*!< 条目头，频段变更条目: 0x07 */
        public UInt16 条目长度;           /*!< 条目长度，单位: 字节，固定取: 0x07 */
        public UInt16 目标频段;          /*!< 目标频段，0: 频段0; 1: 频段1; 2: 频段2 */
        public UInt32 频段切换剩余时间ms;        /*!< 频段切换剩余时间，单位: ms */
    }

    /**
     * 信标条目: 国网频段变更条目(0x07)，参考文档 表39
     */
    public class bitem_chg_band_c_gw
    {
        public byte[] 数据;
        public byte 条目头;                    /*!< 条目头，频段变更条目: 0x07 */
        public UInt16 条目长度;           /*!< 条目长度，单位: 字节，固定取: 0x07 */
        public UInt16 目标频段;          /*!< 目标频段，0: 频段0; 1: 频段1; 2: 频段2 */
        public UInt32 频段切换剩余时间ms;        /*!< 频段切换剩余时间，单位: ms */
    }


    /**
* 信标条目: 无线路由参数条目(0x09)
*/
    public class bitem_rfrt_params_c
    {
        public byte[] 数据;
        public byte 条目头;           /*!< 条目头，路由参数通知条目: 0x09 */
        public UInt16 条目长度;         /*!< 条目长度，单位: 字节，固定取: 0x04 */
        public byte 无线发现列表周期s;        /*!< 无线上发现列表周期长度，单位: 秒 */
        public byte 无线接收率老化周期个数;         /*!< 无线接收率老化周期个数，单位：无线发现列表周期。 */
    }

    /**
* 信标条目: 国网无线路由参数条目(0x09)
*/
    public class bitem_rfrt_params_c_gw
    {
        public byte[] 数据;
        public byte 条目头;           /*!< 条目头，路由参数通知条目: 0x09 */
        public UInt16 条目长度;         /*!< 条目长度，单位: 字节，固定取: 0x04 */
        public byte 无线发现列表周期s;        /*!< 无线上发现列表周期长度，单位: 秒 */
        public byte 无线接收率老化周期个数;         /*!< 无线接收率老化周期个数，单位：无线发现列表周期。 */
    }


    /**
     * 信标条目: 频段探测条目(0x0A)，参考文档 
     */
    public class bitem_band_detect_c
    {
        public byte[] 数据;
        public byte 条目头;            /*!< 条目头，频段探测条目: 0x0A */
        public UInt16 条目长度;        /*!< 条目长度，单位: 字节，固定取: 0x04 */
        public UInt16 目标频段;        /*!< 目标频段，0: 频段0; 1: 频段1; 2: 频段2 */
        public UInt16 lid;             /*!< 频段探测LID，本协议固定取0x77 */
    }

    /**
 * 信标条目: 无线信道变更条目(0x0A)，参考文档 
 */
    public class bitem_rf_chnl_chg_c
    {
        public byte[] 数据;
        public byte 条目头;            /*!< 条目头，无线信道变更条目: 0x0A */
        public UInt16 条目长度;        /*!< 条目长度，单位: 字节，固定取: 0x08 */
        public byte 目标信道编号;      /*!< 目标信道编号 */
        public byte 目标信道option;      /*!< 目标信道编号 */
        public byte resv;      
        public UInt32 信道切换剩余时间s;   /*!< 信道切换剩余时间，距离实施信道切换的剩余时间，单位：1 毫秒 (单位改为s)*/
    }

    /**
* 信标条目: 国网无线信道变更条目(0x0A)，参考文档 
*/
    public class bitem_rf_chnl_chg_c_gw
    {
        public byte[] 数据;
        public byte 条目头;            /*!< 条目头，无线信道变更条目: 0x0A */
        public UInt16 条目长度;        /*!< 条目长度，单位: 字节，固定取: 0x08 */
        public byte 目标信道;      /*!< 目标信道编号 */
        public UInt32 信道切换剩余时间s;   /*!< 信道切换剩余时间，距离实施信道切换的剩余时间，单位：1 毫秒 (单位改为s)*/
    }

    /**
     * 信标条目: 万年历同步条目(0x0B)，参考文档 表40
     */
    public class bitem_calendar_c
    {
        public byte[] 数据;
        public byte 条目头;           /*!< 条目头，万年历同步条目: 0x0B */
        public UInt16 条目长度;       /*!< 条目长度，单位: 字节，固定取: 0x0A */
        public string CCO_万年历;      /*!< 万年历，基于 2000/01/01 00:00:00 */
        public UInt32 CCO_万年历NTB;            /*!< 万年历NTB，CCO万年历 对应的NTB */
    }

    /**
     * 信标条目: 组时隙分配条目(0x80)，参考文档 表40
     */
    public class bitem_zuslot_c
    {
        public byte[] 数据;
        public byte 条目头;           /*!< 条目头，组时隙分配条目: 0x80 */
        public UInt16 条目长度;       /*!< 条目长度，单位: 字节 */
        public UInt32 时隙开始位置us; /*!< 时隙开始，组时隙开始位置，单位: us */
        public UInt32 时隙长度us;     /*!< 时隙长度，组时隙长度，单位: us */
        public List<string> TEI;             /*!< TEI组 */
    }

    /**
     * 信标条目: 分钟采集配置条目(0x81)，参考文档 表40
     */
    public class bitem_mincltcfg_c
    {
        public byte[] 数据;
        public byte 条目头;           /*!< 条目头，组时隙分配条目: 0x81 */
        public UInt16 条目长度;       /*!< 条目长度，单位: 字节 固定值0x03*/
        public byte 分钟采集开关; /*!< 分钟采集开关 */
        public byte 分钟采集上报周期min;     /*!< 分钟采集上报周期（单位min，仅支持1\5\15）*/
        public byte resv;             /*!< TEI组 */
    }

    /**
     * 信标条目: 友讯达私有条目(0xB7)
     */
    public class bitem_yxd_private_c 
    {
        public byte[] 数据;
        public byte 条目头;           /*!< 条目头， 0xB7 */
        public byte 条目长度;         /*!< 条目长度，单位: 字节 固定值16*/
        public byte sta工作模式;      /*!< 0:自适应模式 1：固定HPLC单模 其他值表示保留原来配置 */
        public sbyte sta载波功率;     /*!< 范围-4到10 其他值表示保留原来配置 */
        public sbyte sta无线功率;     /*!< 范围30到48 其他值表示保留原来配置 */
        public byte PCO发送发现列表周期;      /*!< 设置范围：5-20次每个路由周期 其他值表示保留原来配置 */
        public byte STA发送发现列表周期;      /*!< 设置范围：5-20次每个路由周期 其他值表示保留原来配置 */
        public byte STA周期代理变更间隔;      /*!< 设置范围：1-8小时 其他值表示保留原来配置 */
        public byte STA心跳报文发送周期;      /*!< 设置范围：1-8分之一个路由周期 其他值表示保留原来配置 */
        public byte[] resv = new byte[6];     /*!< 保留 */
        public byte crc8;     /*!< 保留 */
    }


    /// <summary>
    /// sof信息
    /// </summary>
    public class sof_pld
    {
        public byte 帧头类型;       /*!< 0：长帧头，其他：短帧头 */
        public UInt16 帧类型;
        public UInt16 ori_s;        /*!< 原始源TEI */
        public UInt16 finl_d;       /*!< 原始目的TEI */
        public byte[] DST_MAC = new byte[6];    /*!< 原始目的MAC地址 */
        public byte[] ORI_MAC = new byte[6];    /*!< 原始源MAC地址 */
        public string detail;

    }

    public class sof_pld_c
    {
        //#if NWSM
        [ScriptIgnore] 
        public mac_hdr_single_t MAC单跳帧头;
        public mac_hdr_single_t_gw 国网MAC单跳帧头;
        public Object 单跳帧;
        //#endif
        [ScriptIgnore]
        public mac_hdr_s_t MAC短帧头;
        [ScriptIgnore]
        public mac_hdr_l_t MAC长帧头;
        public nwk_mng_c MME;
        [ScriptIgnore]
        public aps_mng_c 应用层;
        public mac_hdr_gw GW标准帧;
        public aps_mng_c_gw GW应用层;
    }


    public class mac_hdr_single_t
    {
        public byte[] 原始数据 = new byte[4];
        public byte 帧头类型;       //    :1; /*!< 帧头类型，取1，短帧头(12byte) */
        public byte 版本;           //    :2; /*!< 协议版本，0:保留；1:本标准协议 2单跳协议*/
        public byte resv;           //    :5; /*!< 协议版本，0:保留；1:本标准协议 2单跳协议*/
        public byte MSDU类型;
        public UInt16 MSDU长度;       /*!< MSDU长度 */
    }

    /*国网单跳MAC帧头*/
    public class mac_hdr_single_t_gw
    {
        public byte[] 原始数据 = new byte[4];
        public byte 版本;           //    :2; /*!< 协议版本，0:保留；1:本标准协议 2单跳协议*/
        public byte resv1;           //    :5; /*!< 协议版本，0:保留；1:本标准协议 2单跳协议*/
        public byte 消息类型;
        public UInt16 MSDU长度;       /*!< MSDU长度 */
        public UInt16 resv2;
    }


    /**
     * MAC短帧头结构定义，参考文档 表3
     */
    public class mac_hdr_s_t
    {
        public byte[] 原始数据 = new byte[12];
        public UInt16 帧头类型;//   :1; /*!< 帧头类型，取1，短帧头(12byte) */
        public UInt16 版本;//    :2; /*!< 协议版本，0:保留；1:本标准协议 */
        public UInt16 resv;//       :1; /*!< 保留 */
        public UInt16 MSDU长度;      /*!< MSDU长度 */
        public string 原始目的TEI;//      :12;/*!< 原始目的 TEI */
        public string 原始源TEI;//      :12;/*!< 原始源 TEI */
        public UInt32 SNID;//       :4; /*!< 短网络标识[1~15] */
        public UInt32 重启次数;//     :4; /*!< 重启次数，每次重启+1 */
        public byte 路由跳数;//  :4; /*!< 路由跳数，指MAC帧可以被转发的最大跳数，减1后不为0可转发 */
        public string 广播方向;//  :4; /*!< 广播方向，0:保留; 1:CCO->STA; 2:STA->CCO */
        public string 发送类型;//  :3; /*!< 发送类型，0:单播，1:全网广播，2:本地广播，3:全网确认广播; 4:本地确认广播 */
        public byte 发送次数限值;//        :5; /*!< 发送次数限值，为1表示仅仅发送1次，不重发 */
        public string MSDU序列号;       /*!< MSDU序列号，每一个新的MSDU加1 */
        public byte VLAN标签;
        public byte MSDU类型;
    }

    /**
 * MAC长帧头结构定义，参考文档 表3
 */
    public class mac_hdr_l_t
    {
        public byte[] 原始数据 = new byte[32];
        public UInt16 帧头类型;//   :1; /*!< 帧头类型，取0，长帧头(32byte) */
        public UInt16 协议版本;//    :2; /*!< 协议版本，0:保留；1:本标准协议 */
        public UInt16 resv1;//      :13; /*!< 保留 */
        public UInt16 MSDU长度;      /*!< MSDU长度 */
        public string 原始目的TEI;//      :12;/*!< 原始目的 TEI */
        public string 原始源TEI;//      :12;/*!< 原始源 TEI */
        public UInt32 SNID;//       :4; /*!< 短网络标识[1~15] */
        public UInt32 重启次数;//     :4; /*!< 重启次数，每次重启+1 */
        public byte 路由跳数;//  :4; /*!< 路由跳数，指MAC帧可以被转发的最大跳数，减1后不为0可转发 */
        public string 广播方向;//  :4; /*!< 广播方向，0:保留; 1:CCO->STA; 2:STA->CCO */
        public string 发送类型;//  :3; /*!< 发送类型，0:单播，1:全网广播，2:本地广播，3:全网确认广播; 4:本地确认广播 */
        public byte 发送次数限值;//        :5; /*!< 发送次数限值，为1表示仅仅发送1次，不重发 */
        public string MSDU序列号;       /*!< MSDU序列号，每一个新的MSDU加1 */
        public byte[] dst_mac = new byte[6];    /*!< 目的MAC地址---下一跳的目标MAC地址 */
        public UInt32 时间戳保留;     /*!< 时间戳，保留 */
        public byte[] resv2 = new byte[10];     /*!< 保留 */
        public nwk_hdr_l_c MSDU长帧头;
    }

    /**
     *国网MAC帧头结构定义，标准帧头
     */
    public class mac_hdr_gw
    {
        public byte[] 原始数据 = new byte[28];
        public string 版本;//   :1; /*!< 帧头类型，取0，长帧头(32byte) */
        public string 原始源TEI;//      :12;/*!< 原始源 TEI */
        public string 原始目的TEI;//      :12;/*!< 原始目的 TEI */
        public string 发送类型;//  :3; /*!< 发送类型，0:单播，1:全网广播，2:本地广播，3:全网确认广播; 4:本地确认广播 */
        public byte 发送次数限值;//        :5; /*!< 发送次数限值，为1表示仅仅发送1次，不重发 */
        public byte resv1;
        public string MSDU序列号;       /*!< MSDU序列号，每一个新的MSDU加1 */
        public string MSDU类型;
        public UInt16 MSDU长度;      /*!< MSDU长度 */
        public UInt16 重启次数;//     :4; /*!< 重启次数，每次重启+1 */
        public UInt16 MianFlag;         /*代理路径主标识*/
        public byte 路由跳数;//  :4; /*!< 路由跳数，指MAC帧可以被转发的最大跳数，减1后不为0可转发 */
        public byte 路由剩余跳数;
        public string 广播方向;//  :4; /*!< 广播方向，0:保留; 1:CCO->STA; 2:STA->CCO */
        public byte 路径修复标志; /*路径修复标志*/
        public byte mac地址标志; /*mac地址标志*/
        public byte resv2;
        public byte resv3;
        public byte 组网序列号;
        public byte resv4;
        public byte resv5;
        public byte[] 源mac地址 = new byte[6];//原始源mac地址
        public byte[] 目的mac地址 = new byte[6];//原始目的mac地址
   
        public nwk_hdr_c_gw MSDU标准帧;

    }


    /*管理消息帧格式*/
    public class nwk_hdr_l_c
    {
        public byte[] 原始数据 = new byte[24];
        public byte[] 原始目的地址 = new byte[6];        /*!< 原始目的地址 */
        public byte[] 原始源地址 = new byte[6];        /*!< 原始源地址 */
        public string VLAN;                     /*!< VLAN标签 */
        public string MSDU帧类型;               /*!< MSDU类型 */
        public byte 管理消息版本;
        public object 管理消息类型;
        public byte[] resv = new byte[3];
    }

    /*国网管理消息帧格式*/
    public class nwk_hdr_c_gw
    {
        public byte[] 原始数据 = new byte[4];
        public object 管理消息类型;
        public byte[] resv = new byte[2];
    }


    public class nwk_mng_c
    {
        public string 帧类型;
        public Object 帧荷载信息;
    }



    public class MMAssocReq_c
    {
        public byte[] 原始数据 = new byte[68];
        public byte[] 站点MAC地址 = new byte[6];  /*!< 站点MAC地址 */
        public string PCO;      /*!< 代理TEI，最多携带5个候选代理站点 */
        public string 相线;    /*!< 相线，表示站点所属相线的评估结果 0:未知; 1:A相; 2:B相; 3:C相 */
        public string 设备类型;     /*!< 设备类型 */
        public byte resv1;       /*!< 保留 */
        public byte resv2;       /*!< 保留 */
        public byte MAC地址类型;     /*!< MAC地址类型 0: 电能表地址作为入网MAC地址，适用于电表场景
                                             1: 通信模块本身的MAC作为入网地址，适用于关联表地址失败情形
                                             2: 采集器地址作为入网地址，适用于I和II型采集器场景 */
//#if NWSM
        public string 模块类型;
        public string PCO链路类型;
//#else
//        public byte resv3;       /*!< 保留 */
//#endif
        public string 站点关联随机数;      /*!< 站点关联随机数 */
        public byte[] 厂家附加数据 = new byte[18]; /*!< 厂家附加数据 */
        public string 系统启动原因;
        public string BOOT版本号;
        public string 软件版本号;
        public byte 年;
        public byte 月;
        public byte 日;
        public byte[] 厂商代码 = new byte[2];
        public byte[] 芯片代码 = new byte[2];
        public UInt16 硬复位累积次数;   /*!< 硬复位累积次数 */
        public UInt16 软复位累积次数;   /*!< 软复位累积次数 */
        public byte 代理类型;   /*!< 代理类型 1: 保留; 2: 表示是站点自己动态选择的代理 */
        public byte 组网序列号;      /*!< 组网序列号，每次关联请求时随机产生 */
        public byte resv4;   //:1; /*!< 保留 */
        public byte 管理消息版本;    //:4; /*!< 管理消息版本，本协议固定为1 */
        public byte resv5;    //:3; /*!< 保留 */
        public string 支持的频段;     //:2; /*!< 支持的频段，0:支持频段0/1; 1:支持频段0/1/2 */
        public byte resv6;    //:6; /*!< 保留 */
        public UInt32 端到端序列号;      /*!< 端到端序列号，每次关联请求时随机产生 */
    }

    /*国网管理消息帧-关联请求报文*/
    public class MMAssocReq_c_gw
    {
        public byte[] 原始数据 = new byte[88];
        public byte[] 站点MAC地址 = new byte[6];  /*!< 站点MAC地址 */
        public string PCO;      /*!< 代理TEI，最多携带5个候选代理站点 */
        public string 链路类型;    /*!< 相线，表示站点所属相线的评估结果 0:未知; 1:A相; 2:B相; 3:C相 */
        public byte resv1;     /*!< 设备类型 */
        public string 相线;    /*!< 相线，表示站点所属相线的评估结果 0:未知; 1:A相; 2:B相; 3:C相 */
        public byte resv2;       /*!< 保留 */
        public string 设备类型;     /*!< 设备类型 */
        public byte MAC地址类型;     /*!< MAC地址类型 0: 电能表地址作为入网MAC地址，适用于电表场景
                                             1: 通信模块本身的MAC作为入网地址，适用于关联表地址失败情形
                                             2: 采集器地址作为入网地址，适用于I和II型采集器场景 */
        //#if NWSM
        public string 模块类型;
        // public string PCO链路类型;
        //#else
        //        public byte resv3;       /*!< 保留 */
        //#endif
        public byte resv3;
        public string 站点关联随机数;      /*!< 站点关联随机数 */
        public byte[] 厂家附加数据 = new byte[18]; /*!< 厂家附加数据 */
        public string 系统启动原因;
        public string BOOT版本号;
        public string 软件版本号;
        public byte 年;
        public byte 月;
        public byte 日;
        public byte[] 厂商代码 = new byte[2];
        public byte[] 芯片代码 = new byte[2];
        public UInt16 硬复位累积次数;   /*!< 硬复位累积次数 */
        public UInt16 软复位累积次数;   /*!< 软复位累积次数 */
        public byte 代理类型;   /*!< 代理类型 1: 保留; 2: 表示是站点自己动态选择的代理 */
        //public byte 组网序列号;      /*!< 组网序列号，每次关联请求时随机产生 */
        public UInt32 resv4;   //:1; /*!< 保留 */
        //public byte 管理消息版本;    //:4; /*!< 管理消息版本，本协议固定为1 */
       // public byte resv5;    //:3; /*!< 保留 */
        //public string 支持的频段;     //:2; /*!< 支持的频段，0:支持频段0/1; 1:支持频段0/1/2 */
        //public byte resv6;    //:6; /*!< 保留 */
        public UInt32 端到端序列号;      /*!< 端到端序列号，每次关联请求时随机产生 */
        public byte[] 管理ID信息  = new byte[24];
    }

    public class MMeAssocCnf_c
    {
        public string 提示信息 = "";
        public byte[] 原始数据;
        public byte[] 站点MAC地址 = new byte [6];  /*!< 站点MAC地址 */
        public string 关联结果;      /*!< 结果 */
        public byte 层级;       /*!< 层级 */
        public string TEI;         /*!< TEI，CCO分配的 TEI，读访问时需 &0xfff */
        public string PCO;        /*!< 代理TEI，CCO指定的代理，读访问时需 &0xfff */
        public byte 总分包数;   /*!< 总分包数 */
        public byte 分包序号;      /*!< 分包序号，注意:第一个分包序号为1 */
        public byte 最后一个分包标识;    /*!< 最后一个分包标识，0: 不是最后一个分包; 1: 是最后一个分包 */
#if NWSM
        public string 链路类型;
        public byte 载波频段;
        public byte resv1;       /*!< 保留 */
#else
        public byte resv1;       /*!< 保留 */
#endif
        public string 站点关联随机数;      /*!< 站点关联随机数 */
        public UInt32 重新关联时间ms;     /*!< 重新关联时间，单位: ms */
        public UInt32 端到端序列号;      /*!< 端到端序列号，来自关联请求 */
        public UInt32 路径序号;        /*!< 路径序号，每个关联确认 +1 */
        public byte 组网序列号;      /*!< 组网序列号，来自关联请求 */
        public byte 管理消息版本;    //:4; /*!< 管理消息版本，本协议固定为1 */
        public byte 探测频段标识符; //:1; /*!< 探测频段标识符，0: 非探测频段; 1:探测频段 */
        public byte resv2;    //:3; /*!< 保留 */
        public UInt16 resv3;    /*!< 保留 */

        //路由信息字段
        
        public UInt16 直连站点数;
        public UInt16 直连代理数;
        public UInt16 路由表大小;
        public UInt16 resv4;
        public byte[] 路由表;    
        public List<string> 直连站点;
        public List<connect_pco_c> 直连代理;
    }

    /*国网管理消息帧-关联确认报文*/
    public class MMeAssocCnf_c_gw
    {
        public string 提示信息 = "";
        public byte[] 原始数据;
        public byte[] 站点MAC地址 = new byte[6];  /*!< 站点MAC地址 */
        public byte[] CCO_MAC地址 = new byte[6];  /*!< 站点MAC地址 */
        public string 关联结果;      /*!< 结果 */
        public byte 层级;       /*!< 层级 */
        public string 站点TEI;         /*!< TEI，CCO分配的 TEI，读访问时需 &0xfff */
        public string 链路类型;
        public string 载波频段;
        public byte resv1;       /*!< 保留 */
        public string 代理TEI;        /*!< 代理TEI，CCO指定的代理，读访问时需 &0xfff */
        public byte resv2;
        public byte 总分包数;   /*!< 总分包数 */
        public byte 分包序号;      /*!< 分包序号，注意:第一个分包序号为1 */
        //public byte 最后一个分包标识;    /*!< 最后一个分包标识，0: 不是最后一个分包; 1: 是最后一个分包 */
//#if NWSM
        
       
//#else
        
//#endif
        public string 站点关联随机数;      /*!< 站点关联随机数 */
        public UInt32 重新关联时间ms;     /*!< 重新关联时间，单位: ms */
        public UInt32 端到端序列号;      /*!< 端到端序列号，来自关联请求 */
        public UInt32 路径序号;        /*!< 路径序号，每个关联确认 +1 */
        //public byte 组网序列号;      /*!< 组网序列号，来自关联请求 */
        //public byte 管理消息版本;    //:4; /*!< 管理消息版本，本协议固定为1 */
       // public byte 探测频段标识符; //:1; /*!< 探测频段标识符，0: 非探测频段; 1:探测频段 */
        //public byte resv2;    //:3; /*!< 保留 */
        public UInt32 resv3;    /*!< 保留 */

        //路由信息字段

        public UInt16 直连站点数;
        public UInt16 直连代理数;
        public UInt16 路由表大小;
        public UInt16 resv4;
        public byte[] 路由表;
        public List<string> 直连站点;
        public List<connect_pco_c> 直连代理;
    }

    /**
     * 直连站点信息字段
     */
    public class connect_sta_c
    {

        public string TEI;
#if NWSM
        public string 链路类型;
        public byte resv;
#endif
    }

    public class connect_pco_c
    {
        public string TEI;
#if NWSM
        public string 链路类型;
        public byte resv;
#endif
        public UInt16 子站点数;
        public List<string> 子站点;
    }


    /**
     * 代理变更请求报文，参考文档 表62
     */
    public class MMeChangeProxyReq_s
    {
        public byte[] 原始数据 = new byte[40];
        public string 站点TEI;         /*!< 站点 TEI，申请进行代理变更的站点 TEI */
        public string[] 新代理TEI = new string[5];
        public string 旧代理TEI;    /*!< 旧代理TEI */
        public string 代理类型;    /*!< 代理类型 1: 保留; 2: 表示是站点自己动态选择的代理 */
        public string 原因;       /*!< 原因 */
        public string[] 相线 = new string[3];     /*!< 相线，表示站点所属相线的评估结果 0:未知; 1:A相; 2:B相; 3:C相 */
#if NWSM
        public string[] 新PCO链路类型 = new string[5];
        public byte resv1;        /*!< 保留 */
#else
        public byte resv1;        /*!< 保留 */
#endif
        public UInt32 端到端序列号;      /*!< 端到端序列号，每次关联请求时随机产生 */
        public byte 组网序列号;       /*!< 组网序列号，每次关联请求时随机产生 */
        public byte[] resv2 = new byte[15];    /*!< 保留 */
    }

    /**
     * 国网管理消息帧-代理变更请求报文，参考文档 表62
     */
    public class MMeChangeProxyReq_s_gw
    {
        public byte[] 原始数据 = new byte[24];
        public string 站点TEI;         /*!< 站点 TEI，申请进行代理变更的站点 TEI */
        public byte resv1;        /*!< 保留 */
        public string[] 新代理TEI = new string[5];
        public string[] 链路类型 = new string[5];
        public string 旧代理TEI;    /*!< 旧代理TEI */
        public byte resv2;        /*!< 保留 */
        public string 代理类型;    /*!< 代理类型 1: 保留; 2: 表示是站点自己动态选择的代理 */
        public string 原因;       /*!< 原因 */
        public UInt32 端到端序列号;      /*!< 端到端序列号，每次关联请求时随机产生 */
        public string 相线;     /*!< 相线，表示站点所属相线的评估结果 0:未知; 1:A相; 2:B相; 3:C相 */
        public byte resv3;
        public byte[] resv4 = new byte[3];    /*!< 保留 */

        //#if NWSM


        //#else
        // public byte resv1;        /*!< 保留 */
        //#endif

        //public byte 组网序列号;       /*!< 组网序列号，每次关联请求时随机产生 */
    }

    /**
     * 关联指示报文，参考文档 表57
     */
    public class MMeAssocInd_s
    {
        public string 提示信息 = "";
        public byte[] 原始数据;
        public string 结果;      /*!< 结果 */
        public byte 站点层级;       /*!< 层级 */
        public byte[] 站点MAC地址 = new byte[6];  /*!< 站点MAC地址 */
        public byte[] CCO_MAC = new byte[6];  /*!< CCO MAC地址 */
        public string 站点TEI;         /*!< TEI，CCO分配的 TEI，读访问时需 &0xfff */
        public string 代理TEI;        /*!< 代理TEI，CCO指定的代理，读访问时需 &0xfff */
#if NWSM
        public string 链路类型;
        public byte 载波频段;
        public byte resv;       /*!< 保留 */
        public byte[] resv1 = new byte[2];    /*!< 保留 */
#else
        public byte[] resv1 = new byte[3];    /*!< 保留 */
#endif
        public byte 总分包数;   /*!< 总分包数 */
        public byte 分包序号;      /*!< 分包序号，注意:第一个分包序号为 1 */
        public byte 最后一个分包标识;    /*!< 最后一个分包标识，0: 不是最后一个分包; 1: 是最后一个分包 */
        public string 站点关联随机数;      /*!< 站点关联随机数 */
        public byte[] resv2 = new byte[17];   /*!< 保留 */
        public byte 组网序列号;      /*!< 组网序列号，来自关联请求 */
        public byte[] resv3 = new byte[2];    /*!< 保留 */
        public UInt32 重新关联时间ms;     /*!< 重新关联时间，单位: ms */
        public UInt32 端到端序列号;      /*!< 端到端序列号，来自关联请求 */
        public byte[] resv4 = new byte[8];    /*!< 保留 */
        //路由信息字段

        public UInt16 直连站点数;
        public UInt16 直连代理数;
        public UInt16 路由表大小;
        public UInt16 resv5;
        public byte[] 路由表;
        public List<string> 直连站点;
        public List<connect_pco_c> 直连代理;
    }

    /**
     * 关联汇总指示报文，参考文档 表60
     */
    public class MMeAssocGatherInd_s
    {
        public byte[] 原始数据;
        public byte 结果;      /*!< 结果，固定为0，表示允许加入网络 */
        public byte 站点层级;       /*!< 层级 */
        public byte[] CCO_MAC = new byte[6];  /*!< CCO MAC地址 */
        public string 代理TEI;        /*!< 代理TEI，CCO指定的代理，读访问时需 &0xfff */
        public byte 组网序列号;      /*!< 组网序列号，来自关联请求 */
        public byte 汇总站点数;        /*!< 汇总站点数，最大支持到53个 */
#if NWSM
        public byte 载波频段;
        public byte resv1;
        public byte[] resv = new byte[15];    /*!< 保留 */
#else
        public byte[] resv = new byte[16];    /*!< 保留 */
#endif
        public byte[] 站点信息;
        public List<string> 站点信息字段; /*!< 站点信息 */
    }


    /**
     * 国网关联汇总指示报文，参考文档 表60
     */
    public class MMeAssocGatherInd_s_gw
    {
        public byte[] 原始数据;
        public byte 结果;      /*!< 结果，固定为0，表示允许加入网络 */
        public byte 站点层级;       /*!< 层级 */
        public byte[] CCO_MAC = new byte[6];  /*!< CCO MAC地址 */
        public string 代理TEI;        /*!< 代理TEI，CCO指定的代理，读访问时需 &0xfff */
        public byte 载波频段;
        public byte resv1;
        public byte resv2;
        // public byte 组网序列号;      /*!< 组网序列号，来自关联请求 */
        public byte 汇总站点数;        /*!< 汇总站点数，最大支持到53个 */
        public byte[] resv3 = new byte[4];    /*!< 保留 */
#if NWSM


#else
        public byte[] resv = new byte[16];    /*!< 保留 */
#endif
        public byte[] 站点信息;
        public List<string> 站点信息字段; /*!< 站点信息 */
    }

    public class sta_info_c
    {
        public byte[] MAC_ADDR = new byte[6];
        public string TEI;
    }


    /**
    * 代理变更请求确认报文，参考文档 表66
    */
    public class MMeChangeProxyCnf_s
    {
        public byte[] 原始数据;
        public string 结果;      /*!< 代理变更结果，0: 表示变更成功; 其它值保留 */
        public byte 总分包数;   /*!< 总分包数 */
        public byte 分包序号;      /*!< 分包序号，注意:第一个分包序号为 1 */
        public string 站点TEI;         /*!< TEI，申请代理变更站点的 TEI */
        public string 代理TEI;        /*!< 代理TEI，申请代理变更站点的新代理的 TEI */
        public UInt16 子站点数;     /*!< 子站点数，申请代理变更站点的所有子站点数目 */
        public byte resv1;       /*!< 保留 */
        public byte 组网序列号;      /*!< 组网序列号，来自变更请求报文 */
#if NWSM
        public string 链路类型; 
#endif
        public byte[] resv2 = new byte[2];    /*!< 保留 */
        public UInt32 端到端序列号;      /*!< 端到端序列号，来自变更请求报文 */
        public UInt32 路径序号;        /*!< 路径序号，每个关联确认 +1 */
        public byte[] resv3 = new byte[8];    /*!< 保留 */
        public byte[] 子站点条目;
        public List<substa_info_c> 子站点条目字段; 
    }

    /**
    * 国网代理变更请求确认报文，参考文档 表66
    */
    public class MMeChangeProxyCnf_s_gw
    {
        public byte[] 原始数据;
        public string 结果;      /*!< 代理变更结果，0: 表示变更成功; 其它值保留 */
        public byte 总分包数;   /*!< 总分包数 */
        public byte 分包序号;      /*!< 分包序号，注意:第一个分包序号为 1 */
        public byte resv1;       /*!< 保留 */
        public string 站点TEI;         /*!< TEI，申请代理变更站点的 TEI */
        public string 链路类型;
        public byte resv2;       /*!< 保留 */
        public string 代理TEI;        /*!< 代理TEI，申请代理变更站点的新代理的 TEI */
        public byte resv3;       /*!< 保留 */
        public UInt32 端到端序列号;      /*!< 端到端序列号，来自变更请求报文 */
        public UInt32 路径序号;        /*!< 路径序号，每个关联确认 +1 */
        public UInt16 子站点数;     /*!< 子站点数，申请代理变更站点的所有子站点数目 */
        public byte[] resv4 = new byte[2];
#if NWSM

#endif
        public byte[] 子站点条目;
        public List<substa_info_c> 子站点条目字段;
    }

    public class substa_info_c
    {
        public UInt16 TEI;
    }

    /**
     * 代理变更请求确认报文(位图版)，参考文档 表69
     */
    public class MMeChangeProxyBitMapCnf_s
    {
        public byte[] 原始数据 = new byte[148];
        public string 结果;      /*!< 代理变更结果，0: 表示变更成功; 其它值保留 */
        public string 站点TEI;         /*!< TEI，申请代理变更站点的 TEI */
        public string 代理TEI;        /*!< 代理TEI，申请代理变更站点的新代理的 TEI */
        public byte 组网序列号;      /*!< 组网序列号，来自变更请求报文 */
        public byte[] 子站点位图 = new byte[130];    /*!< 子站点位图，字节0的bit0置1表示TEI为1的站点为此次
                                 发起代理变更的站点的子站点 */
#if NWSM
        public string 链路类型; 
#endif
        public byte resv;        /*!< 保留 */
        public UInt32 端到端序列号;      /*!< 端到端序列号，来自变更请求报文 */
        public UInt32 路径序号;        /*!< 路径序号，每个关联确认 +1 */
    }

    /**
     * 国网代理变更请求确认报文(位图版)，参考文档 表69
     */
    public class MMeChangeProxyBitMapCnf_s_gw
    {
        public byte[] 原始数据 = new byte[20];
        public string 结果;      /*!< 代理变更结果，0: 表示变更成功; 其它值保留 */
        public byte resv1;        /*!< 保留 */
        public string 站点TEI;         /*!< TEI，申请代理变更站点的 TEI */
        public UInt16 位图大小;
        public string 链路类型;
        public byte resv2;
        public string 代理TEI;        /*!< 代理TEI，申请代理变更站点的新代理的 TEI */
        public byte resv3;
        public UInt32 端到端序列号;      /*!< 端到端序列号，来自变更请求报文 */
        public UInt32 路径序号;        /*!< 路径序号，每个关联确认 +1 */
        public UInt32 resv4 ;
        public byte[] 子站点位图 = new byte[80];    /*!< 子站点位图，字节0的bit0置1表示TEI为1的站点为此次
                                 发起代理变更的站点的子站点 */
#if NWSM
        
#endif
       
    }


    /**
     * 离线指示报文，参考文档 表71
     */
    public class MMeLeaveInd_s
    {
        public byte[] 原始数据 = new byte[20];
        public string 站点TEI;         /*!< 站点 TEI，表示要离线的站点TEI, 由CCO发送 */
        public string 离线原因;      /*!< 离线原因 */
        public byte[] 站点MAC地址 = new byte[6];  /*!< 站点MAC地址，表示需要离线的站点的MAC地址 */
        public string 代理TEI;        /*!< 代理 TEI，表示需要离线站点的代理站点 TEI */
        public byte[] resv = new byte[8];     /*!< 保留 */
    }

    /**
     * 延迟离线指示报文，参考文档 表73
     */
    public class MMeDelayLeaveInd_s
    {
        public byte[] 原始数据;
        public string 离线原因;      /*!< 离线原因，这里用 0x3(CCO判断站点不在最新的白名单中) */
        public UInt16 站点总数;      /*!< 站点总数 */
        public UInt16 延迟时间s;       /*!< 延迟时间，单位: 秒 */
        public byte[] resv = new byte[10];    /*!< 保留 */
        public List<sta_mac_addr_c> 站点MAC地址;
    }

    /**
    * 国网离线指示报文，参考文档 表73
    */
    public class MMeDelayLeaveInd_s_gw
    {
        public byte[] 原始数据;
        public string 离线原因;      /*!< 离线原因，这里用 0x3(CCO判断站点不在最新的白名单中) */
        public UInt16 站点总数;      /*!< 站点总数 */
        public UInt16 延迟时间s;       /*!< 延迟时间，单位: 秒 */
        public byte[] resv = new byte[10];    /*!< 保留 */
        public List<sta_mac_addr_c> 站点MAC地址;
    }

    public class sta_mac_addr_c
    {
        public byte[] MAC = new byte[6];
    }


    /**
     * 心跳检测，参考文档 表76
     */
    public class MMeHeartBeatCheck_s
    {
        public byte[] 原始数据 = new byte[139];
        public string 原始源TEI;       /*!< 原始源TEI，初始产生心跳检测报文的站点的 TEI，转发时不变 */
        public string 发现站点数最大的站点TEI;/*!< 发现(discovered)站点数最大的站点 TEI */
        public UInt32 最大的发现站点数;    /*!< 最大的发现站点数 */
        public byte[] 可发现站点TEI = new byte[130];/*!< 可发现站点 TEI，字节0的bit0置1表示可发现TEI为1的站点 */
        public List<string> 详细TEI;
        public byte resv;        /*!< 保留 */
    }

    /**
     * 国网心跳检测，参考文档 表76
     */
    public class MMeHeartBeatCheck_s_gw
    {
        public byte[] 原始数据 ;
        public string 原始源TEI;       /*!< 原始源TEI，初始产生心跳检测报文的站点的 TEI，转发时不变 */
        public byte resv1;
        public string 发现站点数最大的站点TEI;/*!< 发现(discovered)站点数最大的站点 TEI */
        public byte resv2;
        public UInt16 最大的发现站点数;    /*!< 最大的发现站点数 */
        public UInt16 位图大小;
        public byte[] 发现站点位图 ;/*!< 可发现站点 TEI，字节0的bit0置1表示可发现TEI为1的站点 */
        public List<string> 详细TEI;
              
    }

    /**
     * 发现列表报文，参考文档 表77
     */
    public class MMeDiscoverNodeList_s
    {
        public byte[] 原始数据;
        public string TEI;         /*!< TEI，表示发送发现列表报文的站点的 TEI */
        public string 角色;        /*!< 角色，表示发送发现列表报文的站点的角色 */
        public byte 站点层级;       /*!< 层级 */
        public byte[] MAC地址 = new byte[6];      /*!< MAC地址，表示发送发现列表报文的站点的 MAC地址 */
        public string 代理TEI;        /*!< 代理 TEI，表示发送发现列表报文站点的代理 TEI */
        public byte[] resv1 = new byte[4];    //:31;/*!< 保留 */
        public byte 与代理站点通信成功率计算完成标记; //:1; /*!< 与代理站点通信成功率计算完成标记，0:未完成; 1:已完成 */
        public UInt32 与代理站点通信成功率;    /*!< 与代理站点通信成功率(上下行)，取值0~100 */
        public UInt32 与代理站点下行通信成功率;  /*!< 与代理站点下行通信成功率，取值0~100 */
        public UInt16 站点总数;        /*!< 站点总数，表示在发现列表报文中，携带了发现站点信息的站点数量 */
        public UInt16 发送发现列表报文个数;       /*!< 发送发现列表报文个数，表示该站点在上个路由周期内发送的发现列表报文的总数
                                 如果信标帧中"信标使用标记位"为1，则还包含上个路由周期内发送的信标帧个数 */
        public UInt16 上行路由条目总数;    /*!< 上行路由条目总数，表示该站点到达CCO的上行路由表项数目，最大支持4条路由表项 */
        public byte 接收发现列表信息条目长度;   /*!< 接收发现列表信息条目长度，单位: 比特，固定值取8 */
        public UInt16 resv2;       /*!< 保留 */
        public UInt16 路由周期到期剩余时间s;   /*!< 路由周期到期剩余时间，单位: 秒 */
        public byte 相线3;
        public byte 相线2;
        public byte 相线1;
        public byte resv3;
        public byte 最小通信成功率;      /*!< 最小通信成功率，表示该站点到CCO的整个路径的最小通信成功率 */
        public byte[] resv4 = new byte[5];    /*!< 保留 */
        //上行路由条目
        public byte[] 上行路由条目;
        public List<nulroute_info_c> 上行路由信息字段;
        public byte[] 发现站点列表位图 = new byte[128];
        //接收发现列表信息
        public string 接收发现列表信息;
    }

    /**
     * 国网发现列表报文，参考文档 表77
     */
    public class MMeDiscoverNodeList_s_gw
    {
        public byte[] 原始数据;
        public string  TEI;         /*!< TEI，表示发送发现列表报文的站点的 TEI */
        public string  代理TEI;        /*!< 代理 TEI，表示发送发现列表报文站点的代理 TEI */
        public string 角色;        /*!< 角色，表示发送发现列表报文的站点的角色 */
        public byte 站点层级;       /*!< 层级 */
        public byte[] MAC地址 = new byte[6];      /*!< MAC地址，表示发送发现列表报文的站点的 MAC地址 */
        public byte[] CCO_MAC地址 = new byte[6];
        public byte 相线3;
        public byte 相线2;
        public byte 相线1;
        public byte resv1;
        
        //public byte[] resv1 = new byte[4];    //:31;/*!< 保留 */
        public byte 代理站点信道质量; 
        public byte 代理站点通信成功率;    /*!< 与代理站点通信成功率(上下行)，取值0~100 */
        public byte 代理站点下行通信成功率;  /*!< 与代理站点下行通信成功率，取值0~100 */
        public UInt16 站点总数;        /*!< 站点总数，表示在发现列表报文中，携带了发现站点信息的站点数量 */
        public UInt16 发送发现列表报文个数;       /*!< 发送发现列表报文个数，表示该站点在上个路由周期内发送的发现列表报文的总数
                                 如果信标帧中"信标使用标记位"为1，则还包含上个路由周期内发送的信标帧个数 */
        public UInt16 上行路由条目总数;    /*!< 上行路由条目总数，表示该站点到达CCO的上行路由表项数目，最大支持4条路由表项 */
        //public byte 接收发现列表信息条目长度;   /*!< 接收发现列表信息条目长度，单位: 比特，固定值取8 */
        //public UInt16 resv2;       /*!< 保留 */
        public UInt16 路由周期到期剩余时间s;   /*!< 路由周期到期剩余时间，单位: 秒 */
        public UInt16 位图大小;
        public byte 最小通信成功率;      /*!< 最小通信成功率，表示该站点到CCO的整个路径的最小通信成功率 */
        public byte[] resv3 = new byte[3];
        //public byte[] resv4 = new byte[5];    /*!< 保留 */
        //上行路由条目
        public byte[] 上行路由条目;
        public List<nulroute_info_c> 上行路由信息字段;
        public byte[] 发现站点列表位图 ;
        //接收发现列表信息
        public string 接收发现列表信息;
    }

    public class nulroute_info_c
    {
        public string 下一跳TEI;    /*!< 下一跳站点 TEI */
        public string 路由类型;     /*!< 路由类型 */
    }

    public class find_list_info_c
    {
        public string TEI;
        public byte 接收发现列表数;
    }


    /**
     * 通信成功率上报报文，参考文档 表82
     */
    public class MMeSuccessRateReport_s
    {
        public byte[] 原始数据;
        public string TEI;         /*!< 站点 TEI，表示代理站点的 TEI，通信成功率报文，由代理站点发送 */
        public UInt16 站点总数;     /*!< 站点总数，表示代理站点的子站点个数 */
        public List<substa_sr_c> 通信成功率信息;

    }

    /**
     * 国网通信成功率上报报文，参考文档 表82
     */
    public class MMeSuccessRateReport_s_gw
    {
        public byte[] 原始数据;
        public string TEI;         /*!< 站点 TEI，表示代理站点的 TEI，通信成功率报文，由代理站点发送 */
        public byte resv1;
        public UInt16 站点总数;     /*!< 站点总数，表示代理站点的子站点个数 */
        public List<substa_sr_c> 通信成功率信息;

    }



    /**
     * 网络冲突上报报文，参考文档 表82
     */
    public class MMeNidRepeatReport_s
    {
        public byte[] CCO_MAC = new byte[6];
        public byte 邻居网络个数;
        public string 邻居网络;
    }

    /**
    * 国网网络冲突上报报文，参考文档 表82
    */
    public class MMeNidRepeatReport_s_gw
    {
        public byte[] CCO_MAC = new byte[6];
        public byte 邻居网络个数;
        public byte 网络号字节宽度;
        public UInt32[] 邻居网络条目;
    }

    public class substa_sr_c
    {
        public string 子站点TEI;         /*!< 子站点 TEI */
        public byte 下行通信成功率;      /*!< 下行通信成功率 */
        public byte 上行通信成功率;      /*!< 上行通信成功率 */
    }


    /**
     * 过零NTB采集指示报文，参考文档 表84
     */
    public class MMeZeroCrossNTBCollectInd_s
    {
        public byte[] 原始数据 = new byte[5];
        public string 站点TEI;           /*!< 站点 TEI，表示需要过零NTB采集的TEI，广播时填0xFFF */
        public string 站点类型;  /*!< 站点类型，0: 单站点; 1:全网站点 */
        public string 采集周期;/*!< 采集周期，0: 半个电力线周期; 1:一个电力线周期 */
        public byte 采集数量;   /*!< 采集数量 */
    }

    /**
     * 国网过零NTB采集指示报文，参考文档 表84
     */
    public class MMeZeroCrossNTBCollectInd_s_gw
    {
        public byte[] 原始数据 = new byte[5];
        public string 站点TEI;           /*!< 站点 TEI，表示需要过零NTB采集的TEI，广播时填0xFFF */
        public string 站点类型;  /*!< 站点类型，0: 单站点; 1:全网站点 */
        public string 采集周期;/*!< 采集周期，0: 半个电力线周期; 1:一个电力线周期 */
        public byte 采集数量;   /*!< 采集数量 */
    }


    /**
     * 过零NTB上报报文，参考文档 表87
     */
    public class MMeZeroCrossNTBReport_s
    {
        public byte[] 原始数据;
        public string 站点TEI;           /*!< 站点 TEI，表示上报过零NTB信息的站点 TEI */
        public byte 上报数量;        /*!< 上报数量 */
        public byte resv;          /*!< 保留 */
        public UInt32 基准NTB值;         /*!< 基准NTB值 */
        public UInt16[] 过零NTB差值;
        public double[] 过零NTB差值ms;
    }

    /**
     * 国网过零NTB上报报文，参考文档 表87
     */
    public class MMeZeroCrossNTBReport_s_gw
    {
        public byte[] 原始数据;
        public string TEI;           /*!< 站点 TEI，表示上报过零NTB信息的站点 TEI */
        public byte 告知总数量;        /*!< 上报数量 */
        public byte resv;          /*!< 保留 */
        public byte 相线1差值告知数量;
        public byte 相线2差值告知数量;
        public byte 相线3差值告知数量;
        public string 基准NTB值;         /*!< 基准NTB值 */
        public UInt16[] 过零NTB差值;
        public string[] 相线1过零NTB差值;
        public UInt16[] 相线2过零NTB差值;
        public UInt16[] 相线3过零NTB差值;
        // public double[] 过零NTB差值ms;
    }


    public class MMeDiagnose_c
    {
        public string 芯片厂商ID;
        public byte[] 厂家自定义;
    }


    /*国网网络诊断报告*/
    public class MMeDiagnose_c_gw
    {
        public string 芯片厂商ID;
        public byte[] 厂家自定义;
    }


    /*国网路由请求*/
    public class MMeRouteRequest_gw
    {
        public byte[] 原始数据;
        public byte 版本;
        public UInt32 路由请求序列号;
        public byte 路径优选标志;
        public string 负载数据类型;
        public byte 负载数据长度;
        public byte[] 负载数据;
        public List<string> 传播路径列表;
    }


    /*国网路由回复*/
    public class MMeRouteReply_gw
    {
        public byte[] 原始数据;
        public byte 版本;
        public UInt32 路由请求序列号;
        public string 负载数据类型;
        public byte 负载数据长度;
        public byte[] 负载数据;
        public List<string> 传播路径列表;
    }

    /*国网路由错误*/
    public class MMeRouteError_gw
    {
        public byte[] 原始数据;
        public byte 版本;
        public UInt32 路由请求序列号;
        public byte 不可达站点数量;
        public byte[] 不可达站点;
        public List<MMeRouteError_TEI> 不可达站点列表;
    }

    /*国网路由错误-不可达站点列表*/
    public class MMeRouteError_TEI
    {
        public UInt16 TEI;
    }


    /*国网路由应答*/
    public class MMeRouteAck_gw
    {
        public byte[] 原始数据;
        public byte 版本;
        public UInt32 路由请求序列号;
    }


    /*国网链路确认请求*/
    public class MMeLinkConfirmRequest_gw
    {
        public byte[] 原始数据;
        public byte 版本;
        public UInt32 路由请求序列号;
        public byte 确认站点数量;
        public byte[] 确认站点;
        public List<MMeLinkConfirmRequest_TEI> 确认站点列表;
    }

    /*国网链路确认请求-确认站点列表*/
    public class MMeLinkConfirmRequest_TEI
    {
        public UInt16 TEI;
    }

    /*国网链路确认回应*/
    public class MMeLinkConfirmResponse_gw
    {
        public byte[] 原始数据;
        public byte 版本;
        public byte 层级;
        public byte 信道质量;
        public byte 路径优选标志;
        public UInt32 路由请求序列号;
    }


    public class MMeRfConflictRpt_c
    {
        public byte[] 原始数据;
        public byte[] CCO_MAC = new byte[6];
        public byte 邻居网络个数;
        public List<string> 邻居网络条目;
    }



    public class MMeCltDataRpt_c
    {
        public byte[] 原始数据;
        public string TEI;
        public byte resv;
        public byte 报文序号;
        public byte 采集上报报文总数;
    }

    public class MMeRfDiscoverList_c
    {
        public byte[] 原始数据;
        public byte[] 站点MAC地址 = new byte[6];
        public byte 统计序号;
        public List<rfdl_info_c> 信息单元;
    }

    public class rfdl_info_c
    {
        public string 类型;
        public string 长度类型;
        public UInt16 长度;
        public byte[] 数据;
        public Object 内容;
    }

    public class rfdl_sta_info_c
    {
        public byte[] CCO_MAC = new byte[6];
        public string PCO;
        public string 角色;
        public byte 层级;
        public byte 链路RF跳数;
        public byte 代理上行接收率;
        public byte 代理下行接收率;
        public byte 链路最小接收率;
        public byte 无线发现列表周期;
        public byte 无线接收率老化周期个数;
    }

    public class rfdl_rt_info_c
    {
        public List<string> 下一跳站点;
    }

    public class rfdl_nb_chnl_info_c //邻居节点信道信息非位图格式
    {
        public string 信道信息组合类型;
        public byte resv;
        public List<string> 信道信息;
    }

    public class rfdl_nb_chnl_bitinfo_c //邻居节点信道信息位图格式
    {
        public string 信道信息组合类型;
        public byte resv;
        public List<bitinfo_c> 信道信息;
    }

    public class bitinfo_c
    {
        public string 位图起始TEI;
        public byte resv;
        public byte 位图大小;
        public byte[] 位图;
        public List<string> 邻居节点信道信息;
    }


    public class aps_mng_c
    {
        public byte[] 帧头原始数据 = new byte[12];
        public byte 端口号;
        public string 报文标识符;   //固定为0x0101
        public byte resv;
        public byte 帧类型;
        public byte resv2;
        public byte 业务扩展域标识位;
        public byte 响应标识位;
        public byte 启动标志位;
        public byte 传输方向位;
        public byte 业务标识;
        public byte 应用版本号;
        public UInt16 帧序号;
        public UInt16 帧长;
        public byte[] 业务原始数据;
        public byte 具体帧类型;
        public string 帧类型含义;
        public string 解析结果;
        public Object 帧荷载解析;
    }
    /*具体帧类型解释
     * 0：确认帧
     * 1：否认帧
     * 2：设备数据传输
     * 3：模块数据传输
     * 4：查询终端搜索结果
     * 5：下发搜索终端列表
     * 6：下发文件信息
     * 7：下发文件数据
     * 8：查询文件接收状态
     * 9：文件传输完成通知
     * 10:从节点事件设置
     * 11:从节点重启
     * 12:从节点信息查询
     * 13:下发通信地址映射列表
     * 14:查询从节点运行状态信息
     * 15:查询从节点信道信息
     * 16:台区识别
     * 17:相位识别
     * 18:测试帧
     * 19:电表事件上报
     * 20:设备事件上报
     * 21:停复电事上报
     * 22:模块事件上报
     * 23:CKQ-CCO
     * 24:CKQ-serial
     * 25:广播业务
     * 26:数据订阅
     * 255:未知APS帧
     */

    /*
     国网应用层帧格式
     */
    public class aps_mng_c_gw
    {
        public byte[] 帧头原始数据 = new byte[12];
        public byte 报文端口号;
        public UInt16 报文ID;   
        public byte 帧控制;
        /*public byte 帧类型;
        public byte resv2;
        public byte 业务扩展域标识位;
        public byte 响应标识位;
        public byte 启动标志位;
        public byte 传输方向位;
        public byte 业务标识;
        public byte 应用版本号;
        public UInt16 帧序号;
        public UInt16 帧长;
        public byte[] 业务原始数据;*/
        public string 信道安全机制;
        public byte 具体帧类型;
        public string 帧类型含义;
        public string 解析结果;
        public Object 帧荷载解析;
    }


    public class aps_ack_yes_c
    {

    }

    public class aps_ack_no_c
    {
        public string 原因;
    }

    public class aps_to_dev_down_c
    {
        public byte[] 源地址 = new byte[6];
        public byte[] 目的地址 = new byte[6];
        public byte 设备超时时间s;
        public byte resv;
        public UInt16 数据长度;
        public byte[] 数据内容;
    }

    /*国网抄表报文*/
    public class aps_to_dev_down_up_gw
    {
        public byte 协议版本号 ;
        public byte 报文头长度 ;
        public byte 配置字;
        public byte 应答状态;
        public string 转发数据的规约类型;
        public UInt16 转发数据长度;
        public UInt16 报文序号;
        public int 设备超时时间ms;
        public string 选项字;
        public byte 数据长度;        
        public byte[] 数据内容;

        //终端并发抄表独有
        public string 未应答重试标志;
        public string 否认重试标志;
        public UInt16 最大重试次数;
        public int 报文间隔ms;

        public string 传输方向;
    }


    /*从节点主动注册报文*/
    public class slave_node_active_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public string 强制应答标志;
        public byte 从节点参数注册;
        public UInt32 报文序号;
        public byte[] 源MAC地址 = new byte[6];
        public byte[] 目的MAC地址 = new byte[6];

        //查询时，如果是强制应答需要的帧
        public string 状态字段;
        public byte 电能表数量;
        public string 产品类型;
        public byte[] 设备地址 = new byte[6];
        public byte[] 设备ID = new byte[6];
        public byte[] 电能表地址 = new byte[6];
        public string 规约类型;
        public string 模块类型;

        public string 传输方向;
    }


    /*校时报文*/
    public class brd_time_sync_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public UInt16 数据长度;

        public string 传输方向;
    }


    /*通讯测试报文*/
    public class comm_test_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public string 转发数据的规约类型;
        public UInt16 转发数据长度;

        public string 传输方向;
    }


    /*事件上报报文*/
    public class event_report_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public string 方向位;
        public string 启动位;
        public string 功能码;
        public UInt32 转发数据长度;
        public UInt32 报文序号;
        public byte[] 电能表地址 = new byte[6];

        //上报事件扩充
        public byte STA上报事件类型;
        //位图方式上报事件扩充
        public UInt16 发生事件站点起始TEI;
        public byte 发生事件的节点位图;
        //地址方式上报事件扩充
        public UInt16 发生事件的电表个数;
        public UInt16 发生事件的;

        public string 传输方向;
    }

    /*确认/否认报文*/
    public class ack_sack_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public string 方向位;
        public string 确认位;
        public UInt16 报文序号;

        public string 传输方向;
    }

    /*开始升级报文*/
    public class start_upgrade_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public UInt32 升级ID;
        public UInt16 升级时间窗口;
        public UInt16 升级块大小;
        public UInt32 升级文件大小;
        public UInt32 文件CRC校验;

        public string 传输方向;
    }


    /*停止升级报文*/
    public class stop_upgrade_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public UInt32 升级ID;

        public string 传输方向;
    }

    /*传输文件数据（单播）报文*/
    public class trans_upgrade_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public UInt16 数据块大小;
        public UInt32 升级ID;
        public UInt32 数据块编号;

        public string 传输方向;
    }

    /*查询站点升级状态报文*/
    public class request_station_state_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public UInt16 连续查询的块数;
        public UInt32 升级ID;
        public UInt32 起始块号;

        public string 传输方向;
    }

    /*执行升级报文*/
    public class do_upgrade_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public UInt16 等待复位时间;
        public UInt32 升级ID;
        public UInt32 试运行时间;

        public string 传输方向;
    }


    /*查询站点信息报文*/
    public class request_station_info_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public byte 信息列表元素个数;

        public string 传输方向;
    }


    /*抄控器CCO报文*/
    public class ctrl_cco_gw
    {
        public string 协议类型;
        public UInt16 报文头长度;
        public byte[] 报文内容;

        public string 传输方向;
    }

    /*抄控器数据透传报文*/
    public class ctrl_uart_gw
    {
        public string 协议类型;
        public string 启动标志;
        public UInt32 串口波特率;
        public UInt16 报文头长度;
        public byte[] 报文内容;

        public string 传输方向;
    }


    /*台区户变关系识别报文*/
    public class curts_identify_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public string 方向位;
        public string 启动位;
        public string 采集相位;
        public UInt16 报文序号;
        public byte[] MAC地址 = new byte[6];
        public string 特征类型;
        public string 采集类型;
        //台区特征采集启动命令
        public UInt32 起始NTB;
        public byte 采集周期;
        public byte 采集数量;
        public byte 采集序列号;

        //台区特征信息告知报文数据
        public UInt16 TEI;
        public string 采集方式;
        //public byte 采集序列号;
        public byte 告知总数量;
        public UInt32 起始采集NTB1;
        public byte[] 台区特征信息序列1;
        public UInt16 起始采集NTB2;
        public byte[] 台区特征信息序列2;

        //工频电压特征序列号定义
        public byte 第一出线报告数量;
        public byte 第二出线报告数量;
        public byte 第三出线报告数量;
        public string 第一出线电压值;
        public string 第二出线电压值;
        public string 第三出线电压值;

        //工频频率特征序列号定义
        /*public byte 第一出线报告数量;
        public byte 第二出线报告数量;
        public byte 第三出线报告数量;*/
        public string 第一出线频率值;
        public string 第二出线频率值;
        public string 第三出线频率值;

        //工频周期特征序列号定义
        /*public byte 第一出线报告数量;
        public byte 第二出线报告数量;
        public byte 第三出线报告数量;*/
        public string 第一出线周期值;
        public string 第二出线周期值;
        public string 第三出线周期值;

        //台区识别结果报文数据
        //public UInt16 TEI;
        public string 台区判别过程结束标志;
        public string 台区识别结果;
        public byte[] 正确隶属CCO地址 = new byte[6];

        public string 传输方向;
    }


    /*查询ID信息报文*/
    public class read_id_info_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public string 方向位;
        public string ID类型;
        public UInt16 报文序号;
        public byte ID长度;
        public byte[] ID信息 = new byte[24];
        public string 设备类型;
        public string 传输方向;
    }

    /*精准校时报文*/
    public class accurate_timing_gw
    {
        public byte 协议版本号;
        public byte 报文头长度;
        public UInt16 转发数据长度;
        public byte 报文序号;
        public UInt32 NTB;

        public string 传输方向;
    }


    public class aps_to_dev_up_c
    {
        public byte[] 源地址 = new byte[6];
        public byte[] 目的地址 = new byte[6];
        public UInt16 resv;
        public UInt16 数据长度;
        public byte[] 数据内容;
    }

    public class aps_to_mdl_c
    {
        public byte[] 源地址 = new byte[6];
        public byte[] 目的地址 = new byte[6];
        public byte resv;
        public string 业务代码;
        public UInt16 数据长度;
        public byte[] 数据内容;
        public load_clt_c 负荷曲线;
        //public string 内容含义;
    }

    public class load_clt_c
    {
        public string 功能码;
        public string 表类型;
        public string 起始点时间;
        public byte 采集点数量;
        public byte 采集时间间隔;
        public byte 数据项数量;
        public List<string> 数据项;
    }

    public class clt_data_info_c
    {
        public string ID;
        //public List<string> 内容;
    }


    public class aps_search_c
    {
        public byte 终端数量;
        public byte[] resv = new byte[3];
        public List<term_info_c> 终端信息;
    }

    public class term_info_c
    {
        public byte[] 终端地址 = new byte[6];
        public string 规约类型;
        public byte resv;
    }

    public class aps_file_trans_c
    {
        public byte 文件传输信息ID;
        public byte[] resv = new byte[3];
        public string ID具体含义;
        public Object 文件传输信息;

    }
    
    public class trans_info_down_c
    {
        public string 文件性质;
        public byte resv;
        public byte[] 目的地址 = new byte[6];
        public string 文件总校验;
        public UInt32 文件大小;
        public UInt16 文件总段数;
        public UInt16 文件传输时间窗min;
        public UInt32 文件传输ID;
    }

    public class trans_info_up_c
    {
        public UInt32 文件传输ID;
        public UInt16 结果码;
        public UInt16 错误代码;
    }

    public class trans_data_down_c
    {
        public UInt16 文件段号;
        public UInt16 文件总段数;
        public UInt32 文件传输ID;
        public UInt16 文件长度;
        public byte[] 文件段内容;
    }

    public class trans_data_up_c
    {
        public UInt32 文件传输ID;
        public UInt32 结果码;
    }

    public class trans_recv_stat_down_c
    {
        public UInt32 文件传输ID;
        public UInt16 起始段号;
        public UInt16 连续N个文件段状态位;
    }

    public class trans_recv_stat_up_c
    {
        public UInt32 文件传输ID;
        public UInt16 起始段号;
        public byte 文件传输状态;
        public byte resv;
        public byte[] 连续N个文件段状态;
    }

    public class trans_over_down_c
    {
        public UInt32 文件传输ID;
        public UInt16 延时启用时间s;
    }

    public class trans_over_up_c
    {
        public UInt32 文件传输ID;
        public UInt32 结果码;
    }


    public class aps_slave_sta_evt_c
    {
        public string 从节点事件标识;
        public byte[] resv = new byte[3];
    }

    public class aps_sta_reboot_c
    {
        public byte 延时重启时间s;
        public byte[] resv = new byte[3];
    }

    public class aps_sta_info_query_down_c
    {
        public byte 信息列表元素数量;
        public byte[] 信息元素ID;
    }

    public class aps_sta_info_query_up_c
    {
        public byte 信息列表元素数量;
        public List<info_query_up_c> 信息元素信息;
    }

    public class info_query_up_c
    {
        public string 元素ID;
        public byte 元素数据长度;
        public byte[] 元素数据;
    }


    public class aps_term_map_down_c
    {
        public byte 映射终端数量;
        public byte[] resv = new byte[3];
        public List<term_map_c> 映射终端信息;
    }

    public class term_map_c
    {
        public byte[] 通信地址 = new byte[6];
        public byte[] 终端地址 = new byte[12];
    }


    public class aps_sta_run_info_down_c
    {
        public byte 运行信息列表元素数量;
        public string[] 信息元素ID;
    }

    public class aps_sta_run_info_up_c
    {
        public byte 运行信息列表元素数量;
        public byte[] 信息元素ID;
        public List<run_info_c> 信息元素信息;
    }

    public class run_info_c
    {
        public string 元素ID;
        public byte 元素数据长度;
        public byte[] 运行信息数据;
    }

    public class aps_sta_channel_info_down_c
    {
        public UInt16 周边节点起始序号;
        public byte 查询数量;
    }


    public class aps_sta_channel_info_up_c
    {
        public UInt16 周边节点总数量;
        public byte 本次上报的周边节点数量;
        public List<channel_info_c> 周边节点信道信息;
    }

    public class channel_info_c
    {
        public byte[] 节点地址 = new byte[6];
        public UInt16 节点TEI;
        public UInt16 代理TEI;
        public byte 层级;
        public byte 上行通信成功率;
        public byte 下行通信成功率;
        public byte 上下行通信成功率;
        public int 信噪比;
        public byte 衰减;
    }


    public class aps_ad_phase_c
    {
        public byte 报文头长度;
        public string 采集相位;
        public byte resv;
        public UInt16 resv2;
        public byte[] MAC地址 = new byte[6];
        public string 特征类型;
        public string 采集类型;
        public byte[] 数据;
        public Object 数据具体解析;
    }

    public class ad_satrt_c
    {
        public UInt32 起始NTB;
        public byte 采集周期s;
        public byte 采集数量;
        public byte 采集序列号;
        public byte resv;
    }

    public class ad_feat_rpt_c
    {
        public string TEI;
        public string 采集方式;
        public byte resv;
        public byte 采集序列号;
        public byte 告知总数量;
        public string 起始采集NTB1;
        public feature_seq_c 台区特征信息序列1;
        public string 起始采集NTB2;
        public feature_seq_c 台区特征信息序列2;
    }


    public class feature_seq_c
    {
        public byte resv;
        public string 出线1报告数量;
        public string 出线2报告数量;
        public string 出线3报告数量;
        public string[] 出线 = new string[3];
    }

    public class ad_result_rpt_c
    {
        public string TEI;
        public string 台区判别过程结束标志;
        public string 台区识别结果;
        public byte[] 正确隶属CCO地址 = new byte[6];
    }

    public class phase_clt_c
    {
        public byte 采集数量;
        public byte 采集序列号;
        public byte[] resv = new byte[2];
    }


    public class phase_clt_rpt_c
    {
        public string TEI;
        public string 采集方式;
        public byte resv;
        public byte 采集序列号;
        public byte 告知总数量;
        public UInt32 基准NTB;
        public byte resv1;
        public string 相线1过零NTB差值数量;
        public string 相线2过零NTB差值数量;
        public string 相线3过零NTB差值数量;
        public string 相线1过零差值;
        public string 相线2过零差值;
        public string 相线3过零差值;
    }

    public class aps_test_c
    {
        public string 测试ID;
        public byte resv;
        public UInt16 数据长度;
        public byte[] 数据;
    }

    public class aps_evt_rpt_up_c
    {
        public byte[] 电表地址 = new byte[6];
        public byte[] 电表主动上报报文;
    }

    public class aps_dev_evt_rpt_up_c
    {
        public byte[] 设备地址 = new byte[6];
        public UInt16 resv;
        public byte[] 设备主动上报报文;
    }

    public class aps_power_onoff_c
    {
        public byte 帧头长度;
        public byte 功能码;
        public UInt16 数据长度;
        public byte[] resv = new byte[9];
        public byte[] 数据;
        public List<string> 数据域含义;
        //public string 数据域含义;
    }

    public class aps_mdl_evt_c
    {
        public byte 帧头长度;
        public byte 功能码;
        public UInt16 数据长度;
        public byte[] 数据;
    }

    public class apc_ckq_cco_c
    {
        public byte 协议类型;
        public byte 序号;
        public UInt16 报文长度;
        public byte[] 报文内容;
    }
    public class aps_ckq_serial_c
    {
        public byte 协议类型;
        public string 启动标识;
        public byte resv;
        public UInt32 串口波特率;
        public byte 序号;
        public byte[] resv2 = new byte[3];
        public UInt16 报文长度;
        public byte[] 报文内容;
    }

    public class aps_brd_c
    {
        public byte[] 源地址 = new byte[6];
        public byte[] 目的地址 = new byte[6];
    }

    public class aps_data_subscribe_down_c
    {
        public byte[] 源地址 = new byte[6];
        public byte[] 目的地址 = new byte[6];
        public byte 设备超时时间s;
        public byte resv;
        public UInt16 报文长度;
        public byte[] 报文内容;
    }

    public class aps_data_subscribe_up_c
    {
        public byte[] 源地址 = new byte[6];
        public byte[] 目的地址 = new byte[6];
        public UInt16 resv;
        public UInt16 报文长度;
        public byte[] 报文内容;
    }














    /// <summary>
    /// 帧解析
    /// </summary>
    public static class Parse
    {
        public static string rf_mcs_get(byte mcs)
        {
            string mcs_s = mcs.ToString();
            if (mcs == 0)
            {
                mcs_s = "0[分集次数4" + "调制方式BPSK" + "|码率1/2]";
            }
            else if (mcs == 1)
            {
                mcs_s = "1[分集次数2" + "|调制方式BPSK" + "|码率1/2]";
            }
            else if (mcs == 2)
            {
                mcs_s = "2[分集次数2" + "|调制方式QPSK" + "|码率1/2]";
            }
            else if (mcs == 3)
            {
                mcs_s = "3[分集次数1" + "|调制方式QPSK" + "|码率1/2]";
            }
            else if (mcs == 4)
            {
                mcs_s = "4[分集次数1" + "|调制方式QPSK" + "|码率4/5]";
            }
            else if (mcs == 5)
            {
                mcs_s = "5[分集次数1" + "|调制方式16QAM" + "|码率1/2]";
            }
            else if (mcs == 6)
            {
                mcs_s = "6[分集次数2" + "|调制方式16QAM" + "|码率4/5]";
            }

            return mcs_s;
        }

        /// <summary>
        /// FCH帧解析-BCN
        /// </summary>
        /// <param name="fch"></param>
        /// <returns></returns>
        public static ctrl_beacon fch_bcn(byte[] buf, ref int simple_flag)
        {
            UInt16 tmp16 = 0;
            byte 相线 = 0; 
            ctrl_beacon bcn = new ctrl_beacon();
            if (simple_flag == 1)
            {
                bcn.信标周期计数 = comFunc.ToUInt32(buf, 5);
                tmp16 = comFunc.ToUInt16(buf, 9);
                bcn.源TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                tmp16 = comFunc.ToUInt16(buf, 11);
                相线 = (byte)comFunc.BitField16(tmp16, 10, 2);
                if (相线 == 1)
                {
                    bcn.相线 = "A相";
                }
                else if (相线 == 2)
                {
                    bcn.相线 = "B相";
                }
                else if (相线 == 3)
                {
                    bcn.相线 = "C相";
                }
                else
                {
                    bcn.相线 = "未知相";
                }
                return bcn;
            }

            bcn.原始数据 = comFunc.ByteArryToHexStr(buf);
            bcn.定界符类型 = comFunc.BitField8(buf[0], 0, 3);
            bcn.接入指示 = comFunc.BitField8(buf[0], 3, 1);
            bcn.SNID = comFunc.BitField8(buf[0], 4, 4);
            bcn.信标时间戳 = comFunc.ToUInt32(buf, 1).ToString("X8");
            bcn.信标周期计数 = comFunc.ToUInt32(buf, 5);
            tmp16 = comFunc.ToUInt16(buf, 9);
            bcn.源TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
            bcn.TMI = comFunc.BitField16(tmp16, 12, 4);
            tmp16 = comFunc.ToUInt16(buf, 11);
            bcn.符号数 = comFunc.BitField16(tmp16, 0, 9);
            bcn.rsvd = (byte)comFunc.BitField16(tmp16, 9, 1);
            相线 = (byte)comFunc.BitField16(tmp16, 10, 2);
            if (相线 == 1)
            {
                bcn.相线 = "A相";
            }
            else if (相线 == 2)
            {
                bcn.相线 = "B相";
            }
            else if (相线 == 3)
            {
                bcn.相线 = "C相";
            }
            else
            {
                bcn.相线 = "未知相";
            }
            bcn.标准版本号 = (byte)comFunc.BitField16(tmp16, 12, 4);
            bcn.帧控制校验 = comFunc.ToUInt24(buf, 13).ToString("X6");
            return bcn;
        }

        /// <summary>
        /// FCH帧解析-国网载波BCN
        /// </summary>
        /// <param name="fch"></param>
        /// <returns></returns>
        public static ctrl_beacon_gw fch_bcn_gw(byte[] buf, ref int simple_flag)
        {
            UInt16 tmp16 = 0;
            UInt32 tmp32 = 0;
            byte 相线 = 0;
            ctrl_beacon_gw bcn = new ctrl_beacon_gw();
            if (simple_flag == 1)
            {
                tmp16 = comFunc.ToUInt16(buf, 8);
                bcn.源TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                tmp16 = comFunc.ToUInt16(buf, 10);
                相线 = (byte)comFunc.BitField16(tmp16, 9, 2);
                if (相线 == 1)
                {
                    bcn.相线 = "A相";
                }
                else if (相线 == 2)
                {
                    bcn.相线 = "B相";
                }
                else if (相线 == 3)
                {
                    bcn.相线 = "C相";
                }
                else
                {
                    bcn.相线 = "未知相";
                }
                return bcn;
            }

            bcn.原始数据 = comFunc.ByteArryToHexStr(buf);
            byte bcn_type = comFunc.BitField8(buf[0], 0, 3);
            if (bcn_type == 0)
            {
                bcn.定界符类型 = "信标帧";
            }
            else if (bcn_type == 1)
            {
                bcn.定界符类型 = "SOF帧";
            }
            else if (bcn_type == 2)
            {
                bcn.定界符类型 = "选择确认帧";
            }
            else if (bcn_type == 3)
            {
                bcn.定界符类型 = "网间协调帧";
            }
            else
            {
                bcn.定界符类型 = "未知";
            }
            bcn.网络类型 = comFunc.BitField8(buf[0], 3, 5);
            bcn.NID = comFunc.ByteArryToHexStr_3(buf, 1, 3).PadLeft(8 , '0'); //这里是把他转为16进制，并且补够8位，缺的补0
            bcn.信标时间戳 = comFunc.ToUInt32(buf, 4).ToString("X8");
            tmp16 = comFunc.ToUInt16(buf, 8);
            bcn.源TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
            bcn.分集拷贝基本模式 = comFunc.BitField16(tmp16, 12, 4);
            tmp32 = comFunc.ToUInt32(buf, 10);
            bcn.符号数 = comFunc.BitField32(tmp32, 0, 9);
            bcn.rsvd = (byte)comFunc.BitField32(tmp32, 11, 9);
            相线 = (byte)comFunc.BitField32(tmp32, 9, 2); 
            if (相线 == 1)
            {
                bcn.相线 = "A相";
            }
            else if (相线 == 2)
            {
                bcn.相线 = "B相";
            }
            else if (相线 == 3)
            {
                bcn.相线 = "C相";
            }
            else
            {
                bcn.相线 = "未知相";
            }
            bcn.标准版本号 = (byte)comFunc.BitField32(tmp32, 12, 4);
            bcn.帧控制校验 = comFunc.ToUInt24(buf, 13).ToString("X6");
            return bcn;
        }

        public static ctrl_beacon_rf fch_bcn_rf(byte[] buf, ref int simple_flag)
        {
            UInt16 tmp16 = 0;
            byte pbsize;
            UInt16[] pb_size = {16, 40, 72, 136, 264, 520};
            ctrl_beacon_rf bcn = new ctrl_beacon_rf();
            if (simple_flag == 1)
            {
                bcn.信标周期计数 = comFunc.ToUInt32(buf, 5);
                tmp16 = comFunc.ToUInt16(buf, 9);
                bcn.源TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                tmp16 = comFunc.ToUInt16(buf, 11);
                pbsize = (byte)comFunc.BitField16(tmp16, 0, 4);
                if (pbsize <= 5)
                {
                    bcn.载荷PB大小 = pb_size[pbsize];
                }
                else
                {
                    bcn.载荷PB大小 = 0;
                }
                return bcn;
            }

            bcn.原始数据 = comFunc.ByteArryToHexStr(buf);
            bcn.定界符类型 = comFunc.BitField8(buf[0], 0, 3);
            bcn.接入指示 = comFunc.BitField8(buf[0], 3, 1);
            bcn.SNID = comFunc.BitField8(buf[0], 4, 4);
            bcn.信标时间戳 = comFunc.ToUInt32(buf, 1).ToString("X8");
            bcn.信标周期计数 = comFunc.ToUInt32(buf, 5);
            tmp16 = comFunc.ToUInt16(buf, 9);
            bcn.源TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
            bcn.MCS = rf_mcs_get((byte)comFunc.BitField16(tmp16, 12, 4));

            tmp16 = comFunc.ToUInt16(buf, 11);
            pbsize = (byte)comFunc.BitField16(tmp16, 0, 4);
            if (pbsize <= 5)
            {
                bcn.载荷PB大小 = pb_size[pbsize];
            }
            else
            {
                bcn.载荷PB大小 = 0;
            }

            bcn.resv = comFunc.BitField16(tmp16, 4, 8).ToString("X1");
            bcn.标准版本号 = (byte)comFunc.BitField16(tmp16, 12, 4);
            bcn.帧控制校验 = comFunc.ToUInt24(buf, 13).ToString("X6");
            return bcn;
        }

        /// <summary>
        /// FCH帧解析-国网无线BCN
        /// </summary>
        /// <param name="fch"></param>
        /// <returns></returns>
        public static ctrl_beacon_rf_gw fch_bcn_rf_gw(byte[] buf, ref int simple_flag)
        {
            UInt16 tmp16 = 0;
            byte pbsize;
            UInt16[] pb_size = { 16, 40, 72, 136, 264, 520 };
            ctrl_beacon_rf_gw bcn = new ctrl_beacon_rf_gw();
            if (simple_flag == 1)
            {
               // bcn.信标周期计数 = comFunc.ToUInt32(buf, 5);
                tmp16 = comFunc.ToUInt16(buf, 8);
                bcn.源TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                tmp16 = comFunc.ToUInt16(buf, 10);
                pbsize = (byte)comFunc.BitField16(tmp16, 0, 4);
                if (pbsize <= 5)
                {
                    bcn.载荷PB大小 = pb_size[pbsize];
                }
                else
                {
                    bcn.载荷PB大小 = 0;
                }
                return bcn;
            }

            bcn.原始数据 = comFunc.ByteArryToHexStr(buf);
            byte bcn_type = comFunc.BitField8(buf[0], 0, 3);
            if (bcn_type == 0)
            {
                bcn.定界符类型 = "信标帧";
            }
            else if (bcn_type == 1)
            {
                bcn.定界符类型 = "SOF帧";
            }
            else if (bcn_type == 2)
            {
                bcn.定界符类型 = "选择确认帧";
            }
            else if (bcn_type == 3)
            {
                bcn.定界符类型 = "网间协调帧";
            }
            else
            {
                bcn.定界符类型 = "未知";
            }
            bcn.网络类型 = comFunc.BitField8(buf[0], 3, 5);
            bcn.NID = comFunc.ByteArryToHexStr_3(buf, 1, 3).PadLeft(8, '0'); //这里是把他转为16进制，并且补够8位，缺的补0
            bcn.信标时间戳 = comFunc.ToUInt32(buf, 4).ToString("X8");
            //bcn.信标周期计数 = comFunc.ToUInt32(buf, 5);
            tmp16 = comFunc.ToUInt16(buf, 8);
            bcn.源TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
            bcn.MCS = rf_mcs_get((byte)comFunc.BitField16(tmp16, 12, 4));

            tmp16 = comFunc.ToUInt16(buf, 10);
            pbsize = (byte)comFunc.BitField16(tmp16, 0, 4);
            if (pbsize <= 5)
            {
                bcn.载荷PB大小 = pb_size[pbsize];
            }
            else
            {
                bcn.载荷PB大小 = 0;
            }

            bcn.rsvd = comFunc.BitField16(tmp16, 4, 16).ToString("X1");
            bcn.标准版本号 = (byte)comFunc.BitField32(tmp16, 20, 4);
            bcn.帧控制校验 = comFunc.ToUInt24(buf, 13).ToString("X6");
            return bcn;
        }

        /// <summary>
        /// FCH帧解析-SOF
        /// </summary>
        /// <param name="fch"></param>
        /// <returns></returns>
        public static ctrl_sof fch_sof(byte[] buf, ref int simple_flag)
        {
            UInt32 tmp32;
            ctrl_sof sof = new ctrl_sof();
            if (simple_flag != 1)
            {
                sof.原始数据 = comFunc.ByteArryToHexStr(buf);
            }
            tmp32 = comFunc.ToUInt32(buf, 0);
            sof.定界符类型 = (byte)comFunc.BitField32(tmp32, 0, 3);
            sof.接入指示 = (byte)comFunc.BitField32(tmp32, 3, 1);
            sof.SNID = (byte)comFunc.BitField32(tmp32, 4, 4);
            sof.源TEI = comFunc.BitField32(tmp32, 8, 12).ToString("X3");
            sof.目的TEI = comFunc.BitField32(tmp32, 20, 12).ToString("X3");

            tmp32 = comFunc.ToUInt32(buf, 4);
            sof.链路标识符 = (byte)comFunc.BitField32(tmp32, 0, 8);
            sof.rsvd = (UInt16)comFunc.BitField32(tmp32, 8, 16);
            sof.物理块个数 = (byte)comFunc.BitField32(tmp32, 24, 4);
            sof.TMI = (byte)comFunc.BitField32(tmp32, 28, 4);

            tmp32 = comFunc.ToUInt32(buf, 8);
            sof.帧长10us = (UInt16)comFunc.BitField32(tmp32, 0, 12);
            sof.rsvd2 = (UInt16)comFunc.BitField32(tmp32, 12, 9);
            sof.TEI过滤标志位 = (byte)comFunc.BitField32(tmp32, 21, 1);
            sof.重传标志位 = (byte)comFunc.BitField32(tmp32, 22, 1);
            sof.符号数 = (UInt16)comFunc.BitField32(tmp32, 23, 9);
            sof.TMI_EXT = (byte)comFunc.BitField8(buf[12], 0, 4);
            sof.标准版本号 = (byte)comFunc.BitField8(buf[12], 4, 4);
            sof.帧控制校验 = comFunc.ToUInt24(buf, 13).ToString("X6");

            return sof;
        }

        /// <summary>
        /// FCH帧解析-国网载波SOF
        /// </summary>
        /// <param name="fch"></param>
        /// <returns></returns>
        public static ctrl_sof_gw fch_sof_gw(byte[] buf, ref int simple_flag)
        {
            UInt32 tmp32;
            UInt16 tmp16;
            ctrl_sof_gw sof = new ctrl_sof_gw();
            if (simple_flag != 1)
            {
                sof.原始数据 = comFunc.ByteArryToHexStr(buf);
            }
            tmp32 = comFunc.ToUInt32(buf, 4);
            byte bcn_type = comFunc.BitField8(buf[0], 0, 3);
            if (bcn_type == 0)
            {
                sof.定界符类型 = "信标帧";
            }
            else if (bcn_type == 1)
            {
                sof.定界符类型 = "SOF帧";
            }
            else if (bcn_type == 2)
            {
                sof.定界符类型 = "选择确认帧";
            }
            else if (bcn_type == 3)
            {
                sof.定界符类型 = "网间协调帧";
            }
            else
            {
                sof.定界符类型 = "未知";
            }
            sof.网络类型 = comFunc.BitField8(buf[0], 3, 5);
            sof.NID = comFunc.ByteArryToHexStr_3(buf, 1, 3).PadLeft(8, '0'); //这里是把他转为16进制，并且补够8位，缺的补0
            sof.源TEI = comFunc.BitField32(tmp32, 0, 12).ToString("X3");
            sof.目的TEI = comFunc.BitField32(tmp32, 12, 12).ToString("X3");

            
            sof.链路标识符 = comFunc.BitField8(buf[7], 0, 8);
            tmp16 = comFunc.ToUInt16(buf, 8);
            sof.帧长 = comFunc.BitField16(tmp16, 0, 12);
            sof.物理块个数 = comFunc.BitField16(tmp16, 12, 4);
            
            tmp16 = comFunc.ToUInt16(buf, 10);
            sof.符号数 = comFunc.BitField16(tmp16, 0, 9);
            sof.广播标志位 = comFunc.BitField16(tmp16, 9, 1);
            sof.重传标志位 = comFunc.BitField16(tmp16, 10, 1);
            sof.加密标志位 = comFunc.BitField16(tmp16, 11, 1);
            sof.分集拷贝基本模式 = comFunc.BitField16(tmp16, 12, 4);

            sof.分集拷贝扩展模式 = comFunc.BitField8(buf[12], 0, 4);
            sof.标准版本号 = (byte)comFunc.BitField8(buf[12], 4, 4);
            sof.帧控制校验 = comFunc.ToUInt24(buf, 13).ToString("X8");

            return sof;
        }

        /// <summary>
        /// FCH帧解析-无线SOF
        /// </summary>
        /// <param name="fch"></param>
        /// <returns></returns>
        public static ctrl_sof_rf fch_sof_rf(byte[] buf, ref int simple_flag)
        {
            UInt32 tmp32;
            ctrl_sof_rf sof = new ctrl_sof_rf();
            byte pbsize;
            UInt16[] pb_size = { 16, 40, 72, 136, 264, 520 };
            if (simple_flag != 1)
            {
                sof.原始数据 = comFunc.ByteArryToHexStr(buf);
            }
            tmp32 = comFunc.ToUInt32(buf, 0);
            sof.定界符类型 = (byte)comFunc.BitField32(tmp32, 0, 3);
            sof.接入指示 = (byte)comFunc.BitField32(tmp32, 3, 1);
            sof.SNID = (byte)comFunc.BitField32(tmp32, 4, 4);
            sof.源TEI = comFunc.BitField32(tmp32, 8, 12).ToString("X3");
            sof.目的TEI = comFunc.BitField32(tmp32, 20, 12).ToString("X3");

            tmp32 = comFunc.ToUInt32(buf, 4);
            sof.链路标识符 = (byte)comFunc.BitField32(tmp32, 0, 8);
            sof.帧长100us = (UInt16)comFunc.BitField32(tmp32, 8, 12);
            pbsize = (byte)comFunc.BitField32(tmp32, 20, 4);
            if (pbsize <= 5)
            {
                sof.载荷PB大小 = pb_size[pbsize];
            }
            else
            {
                sof.载荷PB大小 = 0;
            }

            sof.MCS = rf_mcs_get((byte)comFunc.BitField32(tmp32, 24, 4));
            sof.TEI过滤标志位 = (byte)comFunc.BitField32(tmp32, 28, 1);
            sof.重传标志位 = (byte)comFunc.BitField32(tmp32, 29, 1);
            sof.resv1 = (byte)comFunc.BitField32(tmp32, 30, 2);
            sof.resv2 = comFunc.ToUInt32(buf, 8);
            sof.resv3 = (byte)comFunc.BitField8(buf[12], 0, 4);
            sof.标准版本号 = (byte)comFunc.BitField8(buf[12], 4, 4);
            sof.帧控制校验 = comFunc.ToUInt24(buf, 13).ToString("X6");

            return sof;
        }

        /// <summary>
        /// FCH帧解析-国网无线SOF
        /// </summary>
        /// <param name="fch"></param>
        /// <returns></returns>
        public static ctrl_sof_rf_gw fch_sof_rf_gw(byte[] buf, ref int simple_flag)
        {
            UInt32 tmp32;
            UInt16 tmp16;
            ctrl_sof_rf_gw sof = new ctrl_sof_rf_gw();
            byte pbsize;
            UInt16[] pb_size = { 16, 40, 72, 136, 264, 520 };
            if (simple_flag != 1)
            {
                sof.原始数据 = comFunc.ByteArryToHexStr(buf);
            }
            tmp32 = comFunc.ToUInt32(buf, 4);
            byte bcn_type = comFunc.BitField8(buf[0], 0, 3);
            if (bcn_type == 0)
            {
                sof.定界符类型 = "信标帧";
            }
            else if (bcn_type == 1)
            {
                sof.定界符类型 = "SOF帧";
            }
            else if (bcn_type == 2)
            {
                sof.定界符类型 = "选择确认帧";
            }
            else if (bcn_type == 3)
            {
                sof.定界符类型 = "网间协调帧";
            }
            else
            {
                sof.定界符类型 = "未知";
            }
            sof.网络类型 = comFunc.BitField8(buf[0], 3, 5);
            sof.NID = comFunc.ByteArryToHexStr_3(buf, 1, 3).PadLeft(8, '0'); //这里是把他转为16进制，并且补够8位，缺的补0
            sof.源TEI = comFunc.BitField32(tmp32, 0, 12).ToString("X3");
            sof.目的TEI = comFunc.BitField32(tmp32, 12, 12).ToString("X3");
            sof.链路标识符 = comFunc.BitField32(tmp32, 24, 8);

            tmp16 = comFunc.ToUInt16(buf, 8);            
            sof.帧长 = comFunc.BitField16(tmp16, 0, 12);
            pbsize = (byte)comFunc.BitField16(tmp16, 12, 4);
            if (pbsize <= 5)
            {
                sof.载荷PB大小 = pb_size[pbsize];
            }
            else
            {
                sof.载荷PB大小 = 0;
            }

            tmp16 = comFunc.ToUInt16(buf, 10);
            sof.resv1 = comFunc.BitField16(tmp16, 0, 9);
            sof.广播标志位 = comFunc.BitField16(tmp16, 9, 1);
            sof.重传标志位 = comFunc.BitField16(tmp16, 10, 1);
            sof.加密标志位 = comFunc.BitField16(tmp16, 11, 1);
            sof.MCS = comFunc.BitField16(tmp16, 12, 4);
            sof.resv2 = (byte)comFunc.BitField8(buf[12], 0, 4);
            sof.标准版本号 = (byte)comFunc.BitField8(buf[12], 4, 4);
            sof.帧控制校验 = comFunc.ToUInt24(buf, 13).ToString("X6");

            return sof;
        }

        public static ctrl_sack fch_sack(byte[] buf, byte protype, ref string detail)
        {
            UInt16 tmp16 = 0;
            UInt32 tmp32 = 0;
            ctrl_sack sack = new ctrl_sack();
            sack.原始数据 = comFunc.ByteArryToHexStr(buf);
            sack.定界符类型 = comFunc.BitField8(buf[0], 0, 3);
            sack.接入指示 = comFunc.BitField8(buf[0], 3, 1);
            sack.SNID = comFunc.BitField8(buf[0], 4, 4);

            sack.扩展帧类型 = (byte)comFunc.BitField8(buf[12], 0, 4);
            if (sack.扩展帧类型 == 0)
            {
                if (protype == 0)
                {
                    sack.ACK = new sack_c();
                    sack.ACK.接收结果 = comFunc.BitField8(buf[1], 0, 4);
                    sack.ACK.接收状态 = comFunc.BitField8(buf[1], 4, 4);
                    tmp16 = comFunc.ToUInt16(buf, 2);
                    sack.ACK.目的TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                    sack.ACK.接收物理块个数 = (byte)comFunc.BitField16(tmp16, 12, 4);
                    Array.Copy(buf, 4, sack.ACK.rsvd, 0, 8);

                    if (sack.ACK.接收结果 == 1)
                    {
                        detail += "校验错误";
                        detail += "|接收块数:" + sack.ACK.接收物理块个数;
                        detail += "|接收状态:" + sack.ACK.接收状态.ToString("X1");
                    }
                }
                else
                {
                    sack.ACK_RF = new sack_rf_c();
                    sack.ACK_RF.接收结果 = comFunc.BitField8(buf[1], 0, 4);
                    sack.ACK_RF.resv1 = comFunc.BitField8(buf[1], 4, 4);
                    tmp16 = comFunc.ToUInt16(buf, 2);
                    sack.ACK_RF.目的TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                    sack.ACK_RF.resv2 = (byte)comFunc.BitField16(tmp16, 12, 4);
                    Array.Copy(buf, 4, sack.ACK_RF.resv3, 0, 8);

                    if (sack.ACK_RF.接收结果 == 1)
                    {
                        detail += "校验错误";
                    }
                }
                
            }
            else if (sack.扩展帧类型 == 1)
            {
                sack.网络搜索帧 = new search_c();
                Array.Copy(buf, 1, sack.网络搜索帧.目标站点地址, 0, 6);
                tmp32 = comFunc.ToUInt32(buf, 7);
                sack.网络搜索帧.源TEI = comFunc.BitField32(tmp32, 0, 12).ToString("X3");
                sack.网络搜索帧.resv = comFunc.BitField32(tmp32, 12, 20);
                sack.网络搜索帧.序号 = buf[11];
                detail += "序号:" + sack.网络搜索帧.序号;
            }
            else if (sack.扩展帧类型 == 2)
            {
                sack.同步帧 = new sync_c();
                sack.同步帧.时间戳 = comFunc.ToUInt32(buf, 1);
                tmp16 = comFunc.ToUInt16(buf, 5);
                sack.同步帧.源TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                sack.同步帧.resv = (byte)comFunc.BitField16(tmp16, 12, 4);
                sack.同步帧.resv2 = comFunc.ToUInt32(buf, 7);
                sack.同步帧.序号 = buf[11];
                detail += "序号:" + sack.同步帧.序号;

            }
            else if (sack.扩展帧类型 == 10)
            {
                byte slot_type = 0;
                sack.时隙预约帧 = new slot_c();
                tmp32 = comFunc.ToUInt32(buf, 1);
                sack.时隙预约帧.源TEI = comFunc.BitField32(tmp32, 0, 12).ToString("X3");
                sack.时隙预约帧.目的TEI = comFunc.BitField32(tmp32, 12, 12).ToString("X3");
                slot_type = comFunc.BitField8(buf[4], 0, 4);
                if (slot_type == 0)
                {
                    sack.时隙预约帧.预约类型 = "0：采数预约帧，指示向目的tei站点预约采数";
                }
                else if (slot_type == 1)
                {
                    sack.时隙预约帧.预约类型 = "1：接收确认帧，指示源tei站点接收到采数报文，并且继续向目的站点预约采数";
                }
                else if (slot_type == 2)
                {
                    sack.时隙预约帧.预约类型 = "2：无数据上报帧，指示目的tei无数据上报";
                }
                else if (slot_type == 3)
                {
                    sack.时隙预约帧.预约类型 = "3：补发预约帧，表示补发预约时隙";
                }
                else
                {
                    sack.时隙预约帧.预约类型 = "保留:" + slot_type;
                }
                sack.时隙预约帧.层级 = comFunc.BitField8(buf[4], 4, 4);
                tmp16 = comFunc.ToUInt16(buf, 5);
                sack.时隙预约帧.确认补发tei = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                sack.时隙预约帧.接收PB块个数 = (byte)comFunc.BitField16(tmp16, 12, 3);
                sack.时隙预约帧.接收结果 = (byte)comFunc.BitField16(tmp16, 15, 1);
                sack.时隙预约帧.报文索引 = buf[7];
                Array.Copy(buf, 8, sack.时隙预约帧.rsvd, 0, 4);
            }
            sack.标准版本号 = (byte)comFunc.BitField8(buf[12], 4, 4);
            sack.帧控制校验 = comFunc.ToUInt24(buf, 13);
            return sack;
        }

        /*国网选择确认帧解析-载波*/
        public static ctrl_sack_gw fch_sack_gw(byte[] buf, byte protype, ref string detail)
        {
            UInt32 tmp32 = 0;
            //UInt32 tmp32 = 0;
            ctrl_sack_gw sack = new ctrl_sack_gw();
            sack.原始数据 = comFunc.ByteArryToHexStr(buf);
            byte bcn_type = comFunc.BitField8(buf[0], 0, 3);
            if (bcn_type == 0)
            {
                sack.定界符类型 = "信标帧";
            }
            else if (bcn_type == 1)
            {
                sack.定界符类型 = "SOF帧";
            }
            else if (bcn_type == 2)
            {
                sack.定界符类型 = "选择确认帧";
            }
            else if (bcn_type == 3)
            {
                sack.定界符类型 = "网间协调帧";
            }
            else
            {
                sack.定界符类型 = "未知";
            }
            sack.网络类型 = comFunc.BitField8(buf[0], 3, 5);
            sack.NID = comFunc.ByteArryToHexStr_3(buf, 1, 3).PadLeft(8, '0');

            sack.扩展帧类型 = (byte)comFunc.BitField8(buf[12], 0, 4);
            if (sack.扩展帧类型 == 0)
            {
                
                sack.接收结果 = comFunc.BitField8(buf[4], 0, 4);
                sack.接收状态 = comFunc.BitField8(buf[4], 4, 4);
                tmp32 = comFunc.ToUInt32(buf, 5);
                sack.源TEI = comFunc.BitField32(tmp32, 0, 12).ToString("X3");
                sack.目的TEI = comFunc.BitField32(tmp32, 12, 12).ToString("X3");
                sack.接收物理块个数 = comFunc.BitField8(buf[8], 0, 3);
                sack.resv1 = comFunc.BitField8(buf[8], 3, 5);
                sack.信道质量 = comFunc.BitField8(buf[9], 0, 8);
                sack.站点负载 = comFunc.BitField8(buf[10], 0, 8);
                sack.resv2 = comFunc.BitField8(buf[11], 0, 8);
                
                if (sack.接收结果 == 1)
                {
                    detail += "校验错误";
                    detail += "|接收块数:" + sack.接收物理块个数;
                    detail += "|接收状态:" + sack.接收状态.ToString("X1");
                }

            }

            /*else if (sack.扩展帧类型 == 1)
            {
                sack.网络搜索帧 = new search_c();
                Array.Copy(buf, 1, sack.网络搜索帧.目标站点地址, 0, 6);
                tmp32 = comFunc.ToUInt32(buf, 7);
                sack.网络搜索帧.源TEI = comFunc.BitField32(tmp32, 0, 12).ToString("X3");
                sack.网络搜索帧.resv = comFunc.BitField32(tmp32, 12, 20);
                sack.网络搜索帧.序号 = buf[11];
                detail += "序号:" + sack.网络搜索帧.序号;
            }
            else if (sack.扩展帧类型 == 2)
            {
                sack.同步帧 = new sync_c();
                sack.同步帧.时间戳 = comFunc.ToUInt32(buf, 1);
                tmp16 = comFunc.ToUInt16(buf, 5);
                sack.同步帧.源TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                sack.同步帧.resv = (byte)comFunc.BitField16(tmp16, 12, 4);
                sack.同步帧.resv2 = comFunc.ToUInt32(buf, 7);
                sack.同步帧.序号 = buf[11];
                detail += "序号:" + sack.同步帧.序号;

            }
            else if (sack.扩展帧类型 == 10)
            {
                byte slot_type = 0;
                sack.时隙预约帧 = new slot_c();
                tmp32 = comFunc.ToUInt32(buf, 1);
                sack.时隙预约帧.源TEI = comFunc.BitField32(tmp32, 0, 12).ToString("X3");
                sack.时隙预约帧.目的TEI = comFunc.BitField32(tmp32, 12, 12).ToString("X3");
                slot_type = comFunc.BitField8(buf[4], 0, 4);
                if (slot_type == 0)
                {
                    sack.时隙预约帧.预约类型 = "0：采数预约帧，指示向目的tei站点预约采数";
                }
                else if (slot_type == 1)
                {
                    sack.时隙预约帧.预约类型 = "1：接收确认帧，指示源tei站点接收到采数报文，并且继续向目的站点预约采数";
                }
                else if (slot_type == 2)
                {
                    sack.时隙预约帧.预约类型 = "2：无数据上报帧，指示目的tei无数据上报";
                }
                else if (slot_type == 3)
                {
                    sack.时隙预约帧.预约类型 = "3：补发预约帧，表示补发预约时隙";
                }
                else
                {
                    sack.时隙预约帧.预约类型 = "保留:" + slot_type;
                }
                sack.时隙预约帧.层级 = comFunc.BitField8(buf[4], 4, 4);
                tmp16 = comFunc.ToUInt16(buf, 5);
                sack.时隙预约帧.确认补发tei = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                sack.时隙预约帧.接收PB块个数 = (byte)comFunc.BitField16(tmp16, 12, 3);
                sack.时隙预约帧.接收结果 = (byte)comFunc.BitField16(tmp16, 15, 1);
                sack.时隙预约帧.报文索引 = buf[7];
                Array.Copy(buf, 8, sack.时隙预约帧.rsvd, 0, 4);
            }*/
            sack.标准版本号 = (byte)comFunc.BitField32(tmp32, 12, 4);
            sack.帧控制校验 = comFunc.ToUInt24(buf, 13).ToString("X6");
            return sack;
        }


        /*国网选择确认帧解析-无线*/
        public static ctrl_sack_rf_gw fch_sack_rf_gw(byte[] buf, byte protype, ref string detail)
        {
            UInt32 tmp32 = 0;
            //UInt32 tmp32 = 0;
            ctrl_sack_rf_gw sack = new ctrl_sack_rf_gw();
            sack.原始数据 = comFunc.ByteArryToHexStr(buf);
            byte bcn_type = comFunc.BitField8(buf[0], 0, 3);
            if (bcn_type == 0)
            {
                sack.定界符类型 = "信标帧";
            }
            else if (bcn_type == 1)
            {
                sack.定界符类型 = "SOF帧";
            }
            else if (bcn_type == 2)
            {
                sack.定界符类型 = "选择确认帧";
            }
            else if (bcn_type == 3)
            {
                sack.定界符类型 = "网间协调帧";
            }
            else
            {
                sack.定界符类型 = "未知";
            }
            sack.网络类型 = comFunc.BitField8(buf[0], 3, 5);
            sack.NID = comFunc.ByteArryToHexStr_3(buf, 1, 3).PadLeft(8, '0');

            sack.扩展帧类型 = (byte)comFunc.BitField8(buf[12], 0, 4);
            if (sack.扩展帧类型 == 0)
            {

                sack.接收结果 = comFunc.BitField8(buf[4], 0, 4);
                sack.resv1 = comFunc.BitField8(buf[4], 4, 4);
                //sack.接收状态 = comFunc.BitField8(buf[4], 4, 4);
                tmp32 = comFunc.ToUInt32(buf, 5);
                sack.源TEI = comFunc.BitField32(tmp32, 0, 12).ToString("X");
                sack.源TEI = sack.源TEI.PadRight(4, '0'); //为让它在后面补0
                sack.目的TEI = comFunc.BitField32(tmp32, 12, 12).ToString("X");
                sack.目的TEI = sack.目的TEI.PadRight(4, '0');
                //sack.接收物理块个数 = comFunc.BitField8(buf[8], 0, 3);
                sack.resv2 = comFunc.BitField8(buf[8], 0, 8);
                sack.信道质量 = comFunc.BitField8(buf[9], 0, 8);
                sack.站点负载 = comFunc.BitField8(buf[10], 0, 8);
                sack.resv3 = comFunc.BitField8(buf[11], 0, 8);

                if (sack.接收结果 == 1)
                {
                    detail += "校验错误";
                }

            }

            /*else if (sack.扩展帧类型 == 1)
            {
                sack.网络搜索帧 = new search_c();
                Array.Copy(buf, 1, sack.网络搜索帧.目标站点地址, 0, 6);
                tmp32 = comFunc.ToUInt32(buf, 7);
                sack.网络搜索帧.源TEI = comFunc.BitField32(tmp32, 0, 12).ToString("X3");
                sack.网络搜索帧.resv = comFunc.BitField32(tmp32, 12, 20);
                sack.网络搜索帧.序号 = buf[11];
                detail += "序号:" + sack.网络搜索帧.序号;
            }
            else if (sack.扩展帧类型 == 2)
            {
                sack.同步帧 = new sync_c();
                sack.同步帧.时间戳 = comFunc.ToUInt32(buf, 1);
                tmp16 = comFunc.ToUInt16(buf, 5);
                sack.同步帧.源TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                sack.同步帧.resv = (byte)comFunc.BitField16(tmp16, 12, 4);
                sack.同步帧.resv2 = comFunc.ToUInt32(buf, 7);
                sack.同步帧.序号 = buf[11];
                detail += "序号:" + sack.同步帧.序号;

            }
            else if (sack.扩展帧类型 == 10)
            {
                byte slot_type = 0;
                sack.时隙预约帧 = new slot_c();
                tmp32 = comFunc.ToUInt32(buf, 1);
                sack.时隙预约帧.源TEI = comFunc.BitField32(tmp32, 0, 12).ToString("X3");
                sack.时隙预约帧.目的TEI = comFunc.BitField32(tmp32, 12, 12).ToString("X3");
                slot_type = comFunc.BitField8(buf[4], 0, 4);
                if (slot_type == 0)
                {
                    sack.时隙预约帧.预约类型 = "0：采数预约帧，指示向目的tei站点预约采数";
                }
                else if (slot_type == 1)
                {
                    sack.时隙预约帧.预约类型 = "1：接收确认帧，指示源tei站点接收到采数报文，并且继续向目的站点预约采数";
                }
                else if (slot_type == 2)
                {
                    sack.时隙预约帧.预约类型 = "2：无数据上报帧，指示目的tei无数据上报";
                }
                else if (slot_type == 3)
                {
                    sack.时隙预约帧.预约类型 = "3：补发预约帧，表示补发预约时隙";
                }
                else
                {
                    sack.时隙预约帧.预约类型 = "保留:" + slot_type;
                }
                sack.时隙预约帧.层级 = comFunc.BitField8(buf[4], 4, 4);
                tmp16 = comFunc.ToUInt16(buf, 5);
                sack.时隙预约帧.确认补发tei = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                sack.时隙预约帧.接收PB块个数 = (byte)comFunc.BitField16(tmp16, 12, 3);
                sack.时隙预约帧.接收结果 = (byte)comFunc.BitField16(tmp16, 15, 1);
                sack.时隙预约帧.报文索引 = buf[7];
                Array.Copy(buf, 8, sack.时隙预约帧.rsvd, 0, 4);
            }*/
            sack.标准版本号 = (byte)comFunc.BitField32(tmp32, 12, 4);
            sack.帧控制校验 = comFunc.ToUInt24(buf, 13).ToString("X6");
            return sack;
        }

        public static ctrl_coord fch_ccord(byte[] buf)
        {
            int nid = 0;
            UInt16 tmp16;
            UInt32 tmp32;
            ctrl_coord coord = new ctrl_coord();
            coord.原始数据 = comFunc.ByteArryToHexStr(buf);
            coord.定界符类型 = comFunc.BitField8(buf[0], 0, 3);
            coord.接入指示 = comFunc.BitField8(buf[0], 3, 1);
            coord.SNID = comFunc.BitField8(buf[0], 4, 4);
            tmp16 = comFunc.ToUInt16(buf, 1);
            for (int i = 0; i < 16; i++)
            {
                if ((tmp16 & (1 << i)) != 0)
                {
                    nid = i + 1;
                    coord.邻居网络 += nid.ToString() + " ";
                }
            }

            tmp32 = comFunc.ToUInt32(buf, 3);
            coord.本网络无线信道编号 = (byte)comFunc.BitField32(tmp32, 0, 8);
            coord.rsvd = (UInt16)comFunc.BitField32(tmp32, 0, 18);
            coord.持续时间ms = comFunc.BitField32(tmp32, 18, 14) * 40;

            coord.rsvd2 = comFunc.BitField8(buf[7], 0, 1);
            coord.带宽结束标识 = comFunc.BitField8(buf[7], 1, 1) == 1 ? "上个带宽时隙已经结束" : "上个带宽时隙未结束";
            coord.本网络无线option = (byte)comFunc.BitField8(buf[7], 2, 2);
            coord.rsvd3 = comFunc.BitField8(buf[7], 4, 4);
            coord.带宽结束偏移ms = (UInt16)(comFunc.ToUInt16(buf, 8) * 4);
            coord.带宽开始偏移ms = (UInt16)(comFunc.ToUInt16(buf, 10) * 4);

            coord.rsvd4 = (byte)comFunc.BitField8(buf[12], 0, 4);
            coord.标准版本号 = (byte)comFunc.BitField8(buf[12], 4, 4);
            coord.帧控制校验 = comFunc.ToUInt24(buf, 13).ToString("X6");
            return coord;
        }

        /*国网载波网间协调帧解析*/
        public static ctrl_coord_gw fch_ccord_gw(byte[] buf)
        {
            
            ctrl_coord_gw coord = new ctrl_coord_gw();
            coord.原始数据 = comFunc.ByteArryToHexStr(buf);
            byte bcn_type = comFunc.BitField8(buf[0], 0, 3);
            if (bcn_type == 0)
            {
                coord.定界符类型 = "信标帧";
            }
            else if (bcn_type == 1)
            {
                coord.定界符类型 = "SOF帧";
            }
            else if (bcn_type == 2)
            {
                coord.定界符类型 = "选择确认帧";
            }
            else if (bcn_type == 3)
            {
                coord.定界符类型 = "网间协调帧";
            }
            else
            {
                coord.定界符类型 = "未知";
            }
            coord.网络类型 = comFunc.BitField8(buf[0], 3, 5);
            coord.NID = comFunc.ByteArryToHexStr_3(buf, 1, 3).PadLeft(8, '0');
         
            
            coord.接收到的邻居网络号 = comFunc.ToUInt24(buf, 8);
            coord.本网络无线信道编号 = (byte)comFunc.BitField32(buf[11], 0, 8);      
            coord.持续时间ms = comFunc.BitField32(buf[4], 0, 16) * 40;
            coord.带宽开始偏移ms = (UInt16)(comFunc.ToUInt16(buf, 6) * 4);
            coord.rsvd = comFunc.BitField8(buf[12], 0, 4);
            coord.标准版本号 = (byte)comFunc.BitField8(buf[12], 4, 4);
            coord.帧控制校验 = comFunc.ToUInt24(buf, 13).ToString("X6");
            return coord;
        }

        public static int pbsize_get_by_tmi(int banse, int expand)
        {
            int[] nw_baseTmi_PbSize = { 520, 520, 0, 136, 136, 136, 136, 520, 520, 520, 520, 0, 0, 0, 0, 0 };
            int[] nw_extTmi_PbSize = { 0, 520, 520, 520, 520, 520, 520, 0, 0, 0, 136, 136, 136, 136, 136, 0 };
            if (banse <= 10)
                return nw_baseTmi_PbSize[banse];

            if (banse == 15)
                return nw_extTmi_PbSize[expand & 0xf];

            return 0;
        }

        public static int pbsize_get_by_tmi_gw(int banse, int expand)
        {
            int[] nw_baseTmi_PbSize = { 520, 520, 136, 136, 136, 136, 136, 520, 520, 520, 520, 264, 264, 72, 72 };
            int[] nw_extTmi_PbSize = { 520, 520, 520, 520, 520, 520, 136, 136, 136, 136, 136, };
            if (banse <= 14)
                return nw_baseTmi_PbSize[banse];

            if (banse == 15)
                return nw_extTmi_PbSize[expand & 0xf];

            return 0;
        }

        public static int MPDU_check(byte[] buf, int bpsz)
        {
            byte[] fch = new byte[16];
            Array.Copy(buf, fch, fch.Length);
            int simple_flag = 0;

            ctrl_sof ctl_sof = Parse.fch_sof(fch, ref simple_flag) ;
            int pbsize = pbsize_get_by_tmi(ctl_sof.TMI, ctl_sof.TMI_EXT);
            if (pbsize == 0 || ctl_sof.物理块个数 == 0)
                return 0;

            if ((pbsize * ctl_sof.物理块个数 + 16) > bpsz)
            {//MPDU帧长度不够
                return 0;
            }

            if (pbsize == 136)
            {//物理块长度为136时，块有且只有一块
                if (ctl_sof.物理块个数 != 1)
                    return 0;
            }

            for (int i = 0; i < ctl_sof.物理块个数; i++)
            {
                int ssn = comFunc.ToUInt16(buf, 16 + i * pbsize + 4);//跳过4个字节的头部DD
                if (i != ssn) //物理块应该是有序的
                    return 0;

                //物理块校验默认成功，底层模块会过滤
            }

            return pbsize;

        }

        public static int MPDU_check_gw(byte[] buf, int bpsz)
        {
            byte[] fch = new byte[16];
            Array.Copy(buf, fch, fch.Length);
            int simple_flag = 0;

            ctrl_sof_gw ctl_sof = Parse.fch_sof_gw(fch, ref simple_flag);
            int pbsize = pbsize_get_by_tmi_gw(ctl_sof.分集拷贝基本模式, ctl_sof.分集拷贝扩展模式);
            if (pbsize == 0 || ctl_sof.物理块个数 == 0)
                return 0;

          /*  if ((pbsize * ctl_sof.物理块个数 + 16) > bpsz)
            {//MPDU帧长度不够
                return 0;
            }*/

            if (pbsize == 136)
            {//物理块长度为136时，块有且只有一块
                if (ctl_sof.物理块个数 != 1)
                    return 0;
            }

           /* for (int i = 0; i < ctl_sof.物理块个数; i++)
            {
                int ssn = comFunc.ToUInt16(buf, 16 + i * pbsize + 4);//跳过4个字节的头部DD
                if (i != ssn) //物理块应该是有序的
                    return 0;

                //物理块校验默认成功，底层模块会过滤
            }*/

            return pbsize;

        }


        public static int MAC_check(byte[] buf, int bpsz) //传上来的mac帧没有校验，因此默认成功
        {
            //int hdr_len = 12;
            //byte 帧头类型 = comFunc.BitField8(buf[0], 0, 1);

            //if (帧头类型 == 0) //长帧头
            //    hdr_len = 32;

            //UInt32 CRC32 = comFunc.ToUInt32(buf, bpsz - 4);
            //UInt32 cal_crc32 = 0;
            //for (int i = 0; i < (bpsz - hdr_len - 4); i++)
            //{
            //    cal_crc32 += buf[hdr_len + i];
            //}

            //if (CRC32 != cal_crc32)
            //    return 0;

            return 1;

        }

        /// <summary>
        /// 精简信标帧载荷解析
        /// </summary>
        /// <param name="buf"></param>
        /// <param name="bpsz"></param>
        /// <returns></returns>
        public static beacon_pld_jj bcn_payload_jj(byte[] buf, int bpsz, ref int simple_flag, ref string detail)
        {
            beacon_pld_jj bcn = new beacon_pld_jj();
            byte bcn_type = comFunc.BitField8(buf[0], 0, 3);
            if (bcn_type == 0)
            {
                bcn.信标类型 = "发现信标";
            }
            else if (bcn_type == 1)
            {
                bcn.信标类型 = "代理信标";
            }
            else if (bcn_type == 2)
            {
                bcn.信标类型 = "中央信标";
            }
            else
            {
                bcn.信标类型 = "未知信标";
            }
            bcn.组网标志位 = comFunc.BitField8(buf[0], 3, 1) == 0 ? "组网未完" : "组网完成";
            bcn.精简信标标志 = comFunc.BitField8(buf[0], 4, 1);
            bcn.resv1 = comFunc.BitField8(buf[0], 5, 1);
            bcn.开始关联标志位 = comFunc.BitField8(buf[0], 6, 1) == 0 ? "不允许站点发起关联请求" : "允许站点发起关联请求";
            bcn.信标使用标志位 = comFunc.BitField8(buf[0], 7, 1) == 0 ? "不使用" : "使用";
            bcn.组网序列号 = buf[1];
            Array.Copy(buf, 2, bcn.CCO_MAC, 0, 6);
            bcn.信标周期计数 = comFunc.ToUInt32(buf, 8);
            bcn.binfo = new byte[bpsz - 12 - 4];
            Array.Copy(buf, 12, bcn.binfo, 0, bcn.binfo.Length);
            bcn.信标条目数 = bcn.binfo[0];
            if (simple_flag == 1)
            {
                detail += "|" + bcn.组网标志位;
                detail += "|组网seq:" + bcn.组网序列号.ToString();
                detail += "|[CCO ";
                detail += comFunc.ByteArryToHexStrWithoutBlock(bcn.CCO_MAC) + "]";
                detail += "|条目数" + bcn.信标条目数.ToString();
            }

            //信标条目解析
            byte item;
            int idx = 1;
            int len = 0;
            for (int i = 0; i < bcn.信标条目数; i++)
            {
                item = bcn.binfo[idx];
                switch (item)
                {
                    case 0x0E://站点能力以及时隙条目
                        bcn.站点能力及时隙条目 = bcn_bitem_sta_cap_and_slot(bcn.binfo, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|站点能力及时隙[";
                            detail += "PCO:" + bcn.站点能力及时隙条目.PCO;
                            detail += " 层级" + bcn.站点能力及时隙条目.层级;
                            detail += " RF跳数" + bcn.站点能力及时隙条目.链路上RF跳数;
                            detail += " 载波频段" + bcn.站点能力及时隙条目.载波频段;
                            detail += " CSMA时隙开始时间" + bcn.站点能力及时隙条目.CSMA时隙开始时间;
                            detail += " CSMA时隙长度" + bcn.站点能力及时隙条目.CSMA时隙长度 + "ms]";
                        }
                        break;
                    default:  //其他条目
                        detail += "|其他:" + item.ToString();
                        len = bcn.binfo[idx + 1];
                        if ((bcn.binfo.Length - idx) < len)
                            len = 0;//跳出循环
                        break;
                }
                idx += len;
                if (len == 0)
                {
                    break;
                }
            }
            bcn.CRC32 = comFunc.ToUInt32(buf, bpsz - 8).ToString("X8");
            return bcn;
        }

        /// <summary>
        /// 国网精简信标帧载荷解析
        /// </summary>
        /// <param name="buf"></param>
        /// <param name="bpsz"></param>
        /// <returns></returns>
        public static beacon_pld_jj_gw bcn_payload_jj_gw(byte[] buf, int bpsz, ref int simple_flag, ref string detail)
        {
            beacon_pld_jj_gw bcn = new beacon_pld_jj_gw();
            byte bcn_type = comFunc.BitField8(buf[0], 0, 3);
            if (bcn_type == 0)
            {
                bcn.信标类型 = "发现信标";
            }
            else if (bcn_type == 1)
            {
                bcn.信标类型 = "代理信标";
            }
            else if (bcn_type == 2)
            {
                bcn.信标类型 = "中央信标";
            }
            else
            {
                bcn.信标类型 = "未知信标";
            }
            bcn.组网标志位 = comFunc.BitField8(buf[0], 3, 1) == 0 ? "组网未完成" : "组网完成";
            bcn.精简信标标志 = comFunc.BitField8(buf[0], 4, 1) == 0 ? "标准信标帧" : "精简信标" ;
            bcn.resv1 = comFunc.BitField8(buf[0], 5, 1);
            bcn.开始关联标志位 = comFunc.BitField8(buf[0], 6, 1) == 0 ? "不允许站点发起关联请求" : "允许站点发起关联请求";
            bcn.信标使用标志位 = comFunc.BitField8(buf[0], 7, 1) == 0 ? "不使用" : "使用";
            bcn.组网序列号 = buf[1];
            Array.Copy(buf, 2, bcn.CCO_MAC, 0, 6);
            bcn.信标周期计数 = comFunc.ToUInt32(buf, 8);
            bcn.binfo = new byte[bpsz - 12 - 4];
            Array.Copy(buf, 12, bcn.binfo, 0, bcn.binfo.Length);
            bcn.信标条目数 = bcn.binfo[0];
            if (simple_flag == 1)
            {
                detail += "|" + bcn.组网标志位;
                detail += "|组网seq:" + bcn.组网序列号.ToString();
                detail += "|[CCO ";
                detail += comFunc.ByteArryToHexStrWithoutBlock(bcn.CCO_MAC) + "]";
                detail += "|条目数" + bcn.信标条目数.ToString();
            }

            //信标条目解析
            byte item;
            int idx = 1;
            int len = 0;
            for (int i = 0; i < bcn.信标条目数; i++)
            {
                item = bcn.binfo[idx];
                switch (item)
                {
                    case 0x0E://站点能力以及时隙条目
                        bcn.站点能力及时隙条目 = bcn_bitem_sta_cap_and_slot_gw(bcn.binfo, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|站点能力及时隙[";
                            detail += "PCO:" + bcn.站点能力及时隙条目.PCO;
                            detail += " 层级" + bcn.站点能力及时隙条目.层级;
                            detail += " RF跳数" + bcn.站点能力及时隙条目.链路上RF跳数;
                            //detail += " 载波频段" + bcn.站点能力及时隙条目.载波频段;
                            detail += " CSMA时隙开始时间" + bcn.站点能力及时隙条目.CSMA时隙开始时间;
                            detail += " CSMA时隙长度" + bcn.站点能力及时隙条目.CSMA时隙长度 + "ms]";
                        }
                        break;
                    default:  //其他条目
                        detail += "|其他:" + item.ToString();
                        len = bcn.binfo[idx + 1];
                        if ((bcn.binfo.Length - idx) < len)
                            len = 0;//跳出循环
                        break;
                }
                idx += len;
                if (len == 0)
                {
                    break;
                }
            }
            bcn.CRC32 = comFunc.ToUInt32(buf, bpsz - 8).ToString("X8");
            return bcn;
        }

        /// <summary>
        /// 信标帧载荷解析
        /// </summary>
        /// <param name="buf"></param>
        /// <param name="bpsz"></param>
        /// <returns></returns>
        public static beacon_pld bcn_payload(byte[] buf, int bpsz, ref int simple_flag, ref csma_union_c csma_union, ref string detail)
        {
            beacon_pld bcn = new beacon_pld();
            byte bcn_type = comFunc.BitField8(buf[0], 0, 3);
            if (bcn_type == 0)
            {
                bcn.信标类型 = "发现信标";
            }
            else if (bcn_type == 1)
            {
                bcn.信标类型 = "代理信标";
            }
            else if (bcn_type == 2)
            {
                bcn.信标类型 = "中央信标";
            }
            else
            {
                bcn.信标类型 = "未知信标";
            }
            bcn.组网标志位 = comFunc.BitField8(buf[0], 3, 1) == 0 ? "组网未完" : "组网完成";
            if (simple_flag == 1)
            {
                detail += "|" + bcn.组网标志位;
            }
            else
            {
#if NWSM
                bcn.精简信标标志 = comFunc.BitField8(buf[0], 4, 1);
#else
                bcn.resv1 = comFunc.BitField8(buf[0], 4, 1);
#endif
                bcn.多网络优选功能开关 = comFunc.BitField8(buf[0], 5, 1) == 0 ? "未使能网络评估" : "使能网络评估";
                bcn.开始关联标志位 = comFunc.BitField8(buf[0], 6, 1) == 0 ? "不允许站点发起关联请求" : "允许站点发起关联请求";
                bcn.resv2 = comFunc.BitField8(buf[0], 7, 1);
            }
            
            bcn.组网序列号 = buf[1];
            if (simple_flag == 1)
            {
                detail += "|组网seq:" + bcn.组网序列号.ToString();
            }
            
            bcn.SNID = comFunc.BitField8(buf[2], 0, 4);
#if NWSM
            bcn.本网络无线option = comFunc.BitField8(buf[2], 4, 2);
            bcn.resv3 = comFunc.BitField8(buf[2], 6, 4);
            bcn.本网络无线信道编号 = buf[3];
            bcn.resv4 = comFunc.ToUInt16(buf, 4);
#else
            bcn.resv3 = comFunc.BitField8(buf[2], 4, 4);
            bcn.resv4 = comFunc.ToUInt24(buf, 3);
#endif

            bcn.信标管理信息 = new byte[bpsz - 6 - 4];
            Array.Copy(buf, 6, bcn.信标管理信息, 0, bcn.信标管理信息.Length);
            bcn.信标条目数 = bcn.信标管理信息[0];
            if (simple_flag == 1)
                detail += "|条目数" + bcn.信标条目数.ToString();

            //信标条目解析
            byte item;
            int idx = 1;
            int len = 0;
            for (int i = 0; i < bcn.信标条目数; i++)
            {
                item = bcn.信标管理信息[idx];
                switch (item)
                {
                    case 0x01://站点能力条目
                        bcn.站点能力条目 = bcn_bitem_sta_cap(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|站点能力[" + bcn.站点能力条目.相线 + "]";
                        }
                        break;
                    case 0x02://时隙分配条目
                        bcn.时隙分配条目 = bcn_bitem_ts(bcn.信标管理信息, idx, bcn_type, ref simple_flag, ref csma_union, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|时隙分配[信标周期" + bcn.时隙分配条目.信标周期长度ms + "ms";
                            detail += " 信标时隙长度" + bcn.时隙分配条目.信标时隙长度ms + "ms";
#if NWSM
                            detail += " RF信标时隙长度" + bcn.时隙分配条目.RF信标时隙长度ms + "ms";
#endif
                            detail += " CSMA时隙大小" + bcn.时隙分配条目.CSMA时隙大小ms+ "ms]";

                        }
                            
                        break;
                    case 0x06://路由参数条目
                        
                        bcn.路由参数条目 = bcn_rt_params(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|路由参数[CCO ";
                            detail += comFunc.ByteArryToHexStrWithoutBlock(bcn.路由参数条目.CCO_MAC) +"]";
                        }
                        break;
                    case 0x07://频段变更条目
                        bcn.频段变更条目 = bcn_chg_band(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|频段变更[目标频段";
                            detail += bcn.频段变更条目.目标频段 + " 剩余时间" + bcn.频段变更条目.频段切换剩余时间ms/1000 + "s]";
                        }
                        
                        break;
                    case 0x08://保留
                        detail += "|其他:" + item.ToString();
                        len = (int)bcn.信标管理信息[idx + 1] | (int)bcn.信标管理信息[idx + 2] << 8;
                        if ((bcn.信标管理信息.Length - idx) < len)
                            len = 0;//跳出循环
                        break;
#if NWSM
                    case 0x0C://无线路由参数条目
                        bcn.无线路由参数条目 = bcn_rfrt_params(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|无线路由参数";
                        }
                        break;
                    case 0x0D://无线信道变更条目
                        bcn.无线信道变更条目 = bcn_rf_chnl_chg(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|无线信道变更[目标信道";
                            detail += bcn.无线信道变更条目.目标信道编号;
                            detail += " 目标op:";
                            detail += bcn.无线信道变更条目.目标信道option;
                            detail += " 剩余时间:";
                            detail += bcn.无线信道变更条目.信道切换剩余时间s + "s]";
                        }
                        break;
#endif
                    case 0x0A://频段探测条目
#if NWSM
                        if (bcn.信标管理信息[idx+1] == 0x08)    //无线信道变更条目
                        {
                            bcn.无线信道变更条目 = bcn_rf_chnl_chg(bcn.信标管理信息, idx, ref simple_flag, out len);
                            if (simple_flag == 1)
                            {
                                detail += "|无线信道变更[目标信道";
                                detail += bcn.无线信道变更条目.目标信道编号;
                                detail += " 目标op:";
                                detail += bcn.无线信道变更条目.目标信道option;
                                detail += " 剩余时间:";
                                detail += bcn.无线信道变更条目.信道切换剩余时间s + "s]";
                            }
                            break;
                        }
#endif
                        bcn.频段探测条目 = bcn_band_detect(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|频段探测[目标频段";
                            detail += bcn.频段探测条目.目标频段 + "]";
                        }
                        
                        break;
                    case 0x0B://万年历同步条目
                 
                        bcn.万年历条目 = bcn_calendar(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|万年历同步[" + bcn.万年历条目.CCO_万年历 + "]";
                        }
                        break;
                    case 0x80://组时隙分配条目：
                        bcn.组时隙分配目 = bcn_zslot(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|组时隙分配";
                        }
                        break;
                    case 0x81://分钟采集配置条目
                        bcn.分钟采集配置条目 = bcn_mincltcfg(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|分钟采集配置条目[使能:" + bcn.分钟采集配置条目.分钟采集开关 + " 上报周期:" + bcn.分钟采集配置条目.分钟采集上报周期min + "min]";
                        }
                        break;

                    case 0xB7://友讯达扩展条目：读取设置私有信标条目参数
                        bcn.友讯达设置私有参数条目 = bcn_bitem_yxd_private(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|友讯达设置私有参数条目";
                        }
                        break;

                    default:  //其他条目
                        detail += "|其他:" + item.ToString();
                        len = bcn.信标管理信息[idx + 1];
                        if ((bcn.信标管理信息.Length - idx) < len)
                            len = 0;//跳出循环
                        break;
                }
                idx += len;
                if (len  == 0)
                {
                    break;
                }
            }

            bcn.CRC32 = comFunc.ToUInt32(buf, bpsz - 8).ToString("X8");

            return bcn;
        }

        /// <summary>
        /// 国网标准信标帧载荷解析
        /// </summary>
        /// <param name="buf"></param>
        /// <param name="bpsz"></param>
        /// <returns></returns>
        public static beacon_pld_gw bcn_payload_gw(byte[] buf, int bpsz, ref int simple_flag, ref csma_union_c_gw csma_union, ref string detail)
        {
            beacon_pld_gw bcn = new beacon_pld_gw();
            byte bcn_type = comFunc.BitField8(buf[0], 0, 3);
            if (bcn_type == 0)
            {
                bcn.信标类型 = "发现信标";
            }
            else if (bcn_type == 1)
            {
                bcn.信标类型 = "代理信标";
            }
            else if (bcn_type == 2)
            {
                bcn.信标类型 = "中央信标";
            }
            else
            {
                bcn.信标类型 = "未知信标";
            }
            bcn.组网标志位 = comFunc.BitField8(buf[0], 3, 1) == 0 ? "组网未完成" : "组网完成";
            if (simple_flag == 1)
            {
                detail += "|" + bcn.组网标志位;
            }
            else
            {

                bcn.精简信标标志 = comFunc.BitField8(buf[0], 4, 1) == 0 ? "标准信标帧" : "精简信标帧";

                bcn.resv1 = comFunc.BitField8(buf[0], 5, 1);

                //bcn.多网络优选功能开关 = comFunc.BitField8(buf[0], 5, 1) == 0 ? "未使能网络评估" : "使能网络评估";
                bcn.开始关联标志位 = comFunc.BitField8(buf[0], 6, 1) == 0 ? "不允许站点发起关联请求" : "允许站点发起关联请求";
                //bcn.resv2 = comFunc.BitField8(buf[0], 7, 1);
            }

            bcn.组网序列号 = buf[1];
            Array.Copy(buf, 2, bcn.CCO_MAC, 0, 6);
            if (simple_flag == 1)
            {
                detail += "|组网seq:" + bcn.组网序列号.ToString();
            }

            //bcn.SNID = comFunc.BitField8(buf[2], 0, 4);
//#if NWSM
            //bcn.本网络无线option = comFunc.BitField8(buf[2], 4, 2);
            bcn.信标周期计数 = comFunc.ToUInt32(buf, 8);
            bcn.本网络无线信道编号 = buf[12];
            Array.Copy(buf, 13, bcn.resv3, 0, 7);

           // bcn.resv4 = comFunc.ToUInt16(buf, 4);
//#else
            //bcn.resv3 = comFunc.BitField8(buf[2], 4, 4);
            //bcn.resv4 = comFunc.ToUInt24(buf, 3);
//#endif

            bcn.信标管理信息 = new byte[bpsz - 20 - 4];
            Array.Copy(buf, 20, bcn.信标管理信息, 0, bcn.信标管理信息.Length);
            bcn.信标条目数 = bcn.信标管理信息[0];
            if (simple_flag == 1)
                detail += "|条目数" + bcn.信标条目数.ToString();

            //信标条目解析
            byte item;
            int idx = 1;
            int len = 0;
            for (int i = 0; i < bcn.信标条目数; i++)
            {
                item = bcn.信标管理信息[idx];
                switch (item)
                {
                    case 0x00://站点能力条目
                        bcn.站点能力条目 = bcn_bitem_sta_cap_gw(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|站点能力[" + bcn.站点能力条目.相线 + "]";
                        }
                        break;
                   
                    case 0x01://路由参数条目

                        bcn.路由参数条目 = bcn_rt_params_gw(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|路由参数[CCO ";
                           // detail += comFunc.ByteArryToHexStrWithoutBlock(bcn.路由参数条目.CCO_MAC) + "]";
                        }
                        break;

                    case 0x02://频段变更条目
                        bcn.频段变更条目 = bcn_chg_band_gw(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|频段变更[目标频段";
                            detail += bcn.频段变更条目.目标频段 + " 剩余时间" + bcn.频段变更条目.频段切换剩余时间ms / 1000 + "s]";
                        }
                        break;

                    case 0x03://无线路由参数条目
                        bcn.无线路由参数条目 = bcn_rfrt_params_gw(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|无线路由参数";
                        }
                        break;

                        
                    case 0xC1://保留
                        detail += "|其他:" + item.ToString();
                        len = (int)bcn.信标管理信息[idx + 1] | (int)bcn.信标管理信息[idx + 2] << 8;
                        if ((bcn.信标管理信息.Length - idx) < len)
                            len = 0;//跳出循环
                        break;

               
                    case 0x04://无线信道变更条目
                        bcn.无线信道变更条目 = bcn_rf_chnl_chg_gw(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|无线信道变更[目标信道";
                            detail += bcn.无线信道变更条目.目标信道;
                            detail += " 目标op:";
                            //detail += bcn.无线信道变更条目.目标信道option;
                            detail += " 剩余时间:";
                            detail += bcn.无线信道变更条目.信道切换剩余时间s + "s]";
                        }
                        break;

                    case 0xC0://时隙分配条目
                        bcn.时隙分配条目 = bcn_bitem_ts_gw(bcn.信标管理信息, idx, bcn_type, ref simple_flag, ref csma_union, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|时隙分配[信标周期" + bcn.时隙分配条目.信标周期长度ms + "ms";
                            detail += " 信标时隙长度" + bcn.时隙分配条目.信标时隙长度ms + "ms";

                            detail += " RF信标时隙长度" + bcn.时隙分配条目.RF信标时隙长度ms + "ms";

                            detail += " CSMA时隙大小" + bcn.时隙分配条目.CSMA时隙长度ms + "ms]";

                        }
                        break;

                    /* case 0x0A://频段探测条目

                         if (bcn.信标管理信息[idx + 1] == 0x08)    //无线信道变更条目
                         {
                             bcn.无线信道变更条目 = bcn_rf_chnl_chg(bcn.信标管理信息, idx, ref simple_flag, out len);
                             if (simple_flag == 1)
                             {
                                 detail += "|无线信道变更[目标信道";
                                 detail += bcn.无线信道变更条目.目标信道编号;
                                 detail += " 目标op:";
                                 detail += bcn.无线信道变更条目.目标信道option;
                                 detail += " 剩余时间:";
                                 detail += bcn.无线信道变更条目.信道切换剩余时间s + "s]";
                             }
                             break;
                         }

                         bcn.频段探测条目 = bcn_band_detect(bcn.信标管理信息, idx, ref simple_flag, out len);
                         if (simple_flag == 1)
                         {
                             detail += "|频段探测[目标频段";
                             detail += bcn.频段探测条目.目标频段 + "]";
                         }

                         break;*/
                    /*case 0x0B://万年历同步条目

                        bcn.万年历条目 = bcn_calendar(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|万年历同步[" + bcn.万年历条目.CCO_万年历 + "]";
                        }
                        break;*/
                    /*case 0x80://组时隙分配条目：
                        bcn.组时隙分配目 = bcn_zslot(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|组时隙分配";
                        }
                        break;*/
                    /* case 0x81://分钟采集配置条目
                         bcn.分钟采集配置条目 = bcn_mincltcfg(bcn.信标管理信息, idx, ref simple_flag, out len);
                         if (simple_flag == 1)
                         {
                             detail += "|分钟采集配置条目[使能:" + bcn.分钟采集配置条目.分钟采集开关 + " 上报周期:" + bcn.分钟采集配置条目.分钟采集上报周期min + "min]";
                         }
                         break;*/

                    case 0xB7://友讯达扩展条目：读取设置私有信标条目参数
                        bcn.友讯达设置私有参数条目 = bcn_bitem_yxd_private(bcn.信标管理信息, idx, ref simple_flag, out len);
                        if (simple_flag == 1)
                        {
                            detail += "|友讯达设置私有参数条目";
                        }
                        break;

                    default:  //其他条目
                        detail += "|其他:" + item.ToString();
                        len = bcn.信标管理信息[idx + 1];
                        if ((bcn.信标管理信息.Length - idx) < len)
                            len = 0;//跳出循环
                        break;
                }
                idx += len;
                if (len == 0)
                {
                    break;
                }
            }

            bcn.CRC32 = comFunc.ToUInt32(buf, bpsz - 8).ToString("X8");

            return bcn;
        }

        /// <summary>
        /// 信标条目-站点能力以及时隙条目解析
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_sta_cap_and_slot_c bcn_bitem_sta_cap_and_slot(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_sta_cap_and_slot_c item = new bitem_sta_cap_and_slot_c();
            item.条目头 = buf[start + 0];
            item.条目长度 = buf[start + 1];
            tiem_len = item.条目长度;
            if (simple_flag != 1)
            {
                item.原始数据 = new byte[item.条目长度];
                Array.Copy(buf, start, item.原始数据, 0, item.条目长度);
            }

            if (item.条目长度 != 19 || ((buf.Length - start) < item.条目长度)) //固定值0x19
            {
                return item;
            }

            item.TEI = comFunc.BitField16(comFunc.ToUInt16(buf, start + 2), 0, 12).ToString("X3");
            item.PCO = comFunc.BitField32(comFunc.ToUInt32(buf, start + 2), 12, 12).ToString("X3");
            item.角色 = comFunc.BitField8(buf[start + 5], 0, 4);
            item.层级 = comFunc.BitField8(buf[start + 5], 4, 4);
            Array.Copy(buf, start + 6, item.发送信标站点MAC地址, 0, 6);
            item.链路上RF跳数 = comFunc.BitField8(buf[start + 12], 0, 4);
            item.载波频段 = comFunc.BitField8(buf[start + 12], 4, 2);
            item.resv = comFunc.BitField8(buf[start + 12], 6, 2);
            item.CSMA时隙开始时间 =  comFunc.ToUInt32(buf, start + 13).ToString();
            item.CSMA时隙长度 = comFunc.ToUInt16(buf, start + 17);
            return item;
        }

        /// <summary>
        /// 信标条目-国网站点能力以及时隙条目解析
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_sta_cap_and_slot_c_gw bcn_bitem_sta_cap_and_slot_gw(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_sta_cap_and_slot_c_gw item = new bitem_sta_cap_and_slot_c_gw();
            item.条目头 = buf[start + 0];
            item.条目长度 = buf[start + 1];
            tiem_len = item.条目长度;
            if (simple_flag != 1)
            {
                item.原始数据 = new byte[item.条目长度];
                Array.Copy(buf, start, item.原始数据, 0, item.条目长度);
            }

            if (item.条目长度 != 19 || ((buf.Length - start) < item.条目长度)) //固定值0x19
            {
                return item;
            }

            item.TEI = comFunc.BitField16(comFunc.ToUInt16(buf, start + 2), 0, 12).ToString("X3");
            item.PCO = comFunc.BitField32(comFunc.ToUInt32(buf, start + 2), 12, 12).ToString("X3");
            item.角色 = comFunc.BitField8(buf[start + 5], 0, 4);
            item.层级 = comFunc.BitField8(buf[start + 5], 4, 4);
            Array.Copy(buf, start + 6, item.发送信标站点MAC地址, 0, 6);
            item.链路上RF跳数 = comFunc.BitField8(buf[start + 12], 0, 4);
            //item.载波频段 = comFunc.BitField8(buf[start + 12], 4, 2);
            item.resv = comFunc.BitField8(buf[start + 12], 4, 4);
            item.CSMA时隙开始时间 = comFunc.ToUInt32(buf, start + 13).ToString();
            item.CSMA时隙长度 = comFunc.ToUInt16(buf, start + 17);
            return item;
        }

        /// <summary>
        /// 信标条目-站点能力解析
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_sta_cap_c bcn_bitem_sta_cap(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_sta_cap_c sta_cap = new bitem_sta_cap_c();
            byte tmp = 0;
            sta_cap.条目头 = buf[start + 0];
            sta_cap.条目长度 = buf[start + 1];
            tiem_len = sta_cap.条目长度;
            if (simple_flag != 1)
            {
                sta_cap.原始数据 = new byte[sta_cap.条目长度];
                Array.Copy(buf, start, sta_cap.原始数据, 0, sta_cap.条目长度);
            }

            if (sta_cap.条目长度 != 0x16 || ((buf.Length - start) < sta_cap.条目长度)) //固定值0x16
            {
                return sta_cap;
            }
            
            sta_cap.层级 = comFunc.BitField8(buf[start + 2], 0, 6);
            tmp = comFunc.BitField8(buf[start + 2], 6, 2);
            if (tmp == 0)
            {
                sta_cap.相线 = "全相";
            }
            else if (tmp == 1)
            {
                sta_cap.相线 = "A相";
            }
            else if (tmp == 2)
            {
                sta_cap.相线 = "B相";
            }
            else
            {
                sta_cap.相线 = "C相";
            }
            if (simple_flag == 1)
            {
                Array.Copy(buf, start + 6, sta_cap.发送信标站点MAC地址, 0, 6);
                tiem_len = buf[start + 1];
                return sta_cap;
            }

            sta_cap.TEI = comFunc.BitField16(comFunc.ToUInt16(buf, start + 3), 0, 12).ToString("X3");
            sta_cap.角色 = comFunc.BitField16(comFunc.ToUInt16(buf, start + 3), 12, 4);
            sta_cap.信标使用标志位 = buf[start + 5] == 0 ? "不使用信标评估信道" : "使用信标评估信道";
            Array.Copy(buf, start + 6, sta_cap.发送信标站点MAC地址, 0, 6);
            sta_cap.代理节点TEI = comFunc.BitField16(comFunc.ToUInt16(buf, start + 12), 0, 12).ToString("X3");
#if NWSM
            sta_cap.链路上RF跳数 = (byte)comFunc.BitField16(comFunc.ToUInt16(buf, start + 12), 12, 4);
#else
            sta_cap.resv1 = comFunc.BitField16(comFunc.ToUInt16(buf, start + 12), 12, 4);
#endif
            sta_cap.最低成功率 = comFunc.ToUInt32(buf, start + 14);
            sta_cap.resv2 = comFunc.ToUInt32(buf, start + 18);

            return sta_cap;
        }

        /// <summary>
        /// 信标条目-国网站点能力解析
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_sta_cap_c_gw bcn_bitem_sta_cap_gw(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_sta_cap_c_gw sta_cap = new bitem_sta_cap_c_gw();
            byte tmp = 0;
            byte tmp2 = 0;
            sta_cap.条目头 = buf[start + 0];
            sta_cap.条目长度 = buf[start + 1];
            tiem_len = sta_cap.条目长度;
            if (simple_flag != 1)
            {
                sta_cap.原始数据 = new byte[sta_cap.条目长度];
                Array.Copy(buf, start, sta_cap.原始数据, 0, sta_cap.条目长度);
            }

            if (sta_cap.条目长度 != 0x0F || ((buf.Length - start) < sta_cap.条目长度)) //固定值0x16
            {
                return sta_cap;
            }

            sta_cap.层级数 = comFunc.BitField8(buf[start + 12], 4, 4);
            tmp = comFunc.BitField8(buf[start + 14], 0, 2);
            if (tmp == 0)
            {
                sta_cap.相线 = "全相";
            }
            else if (tmp == 1)
            {
                sta_cap.相线 = "A相";
            }
            else if (tmp == 2)
            {
                sta_cap.相线 = "B相";
            }
            else
            {
                sta_cap.相线 = "C相";
            }

            if (simple_flag == 1)
            {
                Array.Copy(buf, start + 6, sta_cap.发送信标站点MAC地址, 0, 6);
                tiem_len = buf[start + 1];
                return sta_cap;
            }

            sta_cap.TEI = comFunc.BitField16(comFunc.ToUInt16(buf, start + 2), 0, 12).ToString("X3");
            sta_cap.代理站点TEI = comFunc.BitField16(comFunc.ToUInt16(buf, start + 3), 12, 12).ToString("X3") ;
            //sta_cap.信标使用标志位 = buf[start + 5] == 0 ? "不使用信标评估信道" : "使用信标评估信道";
            sta_cap.最低成功率 = buf[start + 5];
            Array.Copy(buf, start + 6, sta_cap.发送信标站点MAC地址, 0, 6);
            
            tmp2 = comFunc.BitField8(buf[start + 12], 0, 4);
            if (tmp2 == 0)
            {
                sta_cap.角色 = "未知";
            }
            else if (tmp2 == 1)
            {
                sta_cap.角色 = "STA";
            }
            else if (tmp2 == 2)
            {
                sta_cap.角色 = "PCO";
            }
            else if (tmp2 == 4)
            {
                sta_cap.角色 = "CCO";
            }

            sta_cap.代理站点信道质量 =buf[start + 13];
            sta_cap.链路上RF跳数 = (byte)comFunc.BitField16(comFunc.ToUInt16(buf, start + 14), 2, 4);

            sta_cap.resv1 = (byte)comFunc.BitField16(comFunc.ToUInt16(buf, start + 14), 6, 2);

            //sta_cap.resv2 = comFunc.ToUInt32(buf, start + 18);

            return sta_cap;
        }

        /// <summary>
        /// 信标条目-时隙分配条目解析
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_ts_c bcn_bitem_ts(byte[] buf, int start, byte bcn_type, ref int simple_flag, ref csma_union_c csma_union, out int tiem_len)
        {
            string[] rf_bcn_type = {"HPLC", "RF标准", "HPLC,该时隙结束后发送RF标准", "HPLC,该时隙结束后发送RF精简", "HPLC,CSMA时隙发送RF精简",
                                                      "HPLC,CSMA时隙发送RF标准", "HPLC,该时隙同步发送RF精简"};
            bitem_ts_c ts_hdr = new bitem_ts_c();
            ts_hdr.条目头 = buf[start + 0];
            ts_hdr.条目长度 = comFunc.ToUInt16(buf, start + 1);
            tiem_len = ts_hdr.条目长度;
            if (simple_flag != 1)
            {
                ts_hdr.数据 = new byte[tiem_len];
                Array.Copy(buf, start, ts_hdr.数据, 0, tiem_len);
            }
            if (ts_hdr.条目长度 < 27 || ((buf.Length - start) < ts_hdr.条目长度))
            {
                return ts_hdr;
            }

            ts_hdr.非中央信标时隙总数 = buf[start + 3];
            ts_hdr.中央信标时隙总数 = buf[start + 4];
            ts_hdr.CSMA相线个数 = buf[start + 5];
            ts_hdr.代理信标时隙总数 = buf[start + 6];
            ts_hdr.信标时隙长度ms = (double)comFunc.ToUInt16(buf, start + 7) * 100 / 1000;
            ts_hdr.CSMA时隙大小ms = (UInt16)((UInt16)buf[start + 9] * 10);
            ts_hdr.BCSMA相线个数 = buf[start + 10];
            ts_hdr.BCSMA_lid = buf[start + 11];
            ts_hdr.TDMA时隙长度ms = (double)comFunc.ToUInt16(buf, start + 12) * 100 / 1000;
            ts_hdr.TDMA_LID = buf[start + 14];
            ts_hdr.NTB = comFunc.ToUInt32(buf, start + 15);
            ts_hdr.信标周期长度ms = (double)comFunc.ToUInt32(buf, start + 19) * 100 /1000;
#if NWSM
            ts_hdr.resv = comFunc.ToUInt32(buf, start + 23);
            ts_hdr.RF信标时隙长度ms = (UInt16)comFunc.BitField32(ts_hdr.resv, 0, 10);
            ts_hdr.resv = ts_hdr.resv >> 10;
#else
            ts_hdr.resv = comFunc.ToUInt32(buf, start + 23);
#endif


            int idx = start + 27;
            //非中央信标信息
            if (ts_hdr.非中央信标时隙总数 != 0 && bcn_type != 0)
            {
                ts_hdr.非中央信标时隙信息字段 = new byte[ts_hdr.非中央信标时隙总数 * 2];
                Array.Copy(buf, idx, ts_hdr.非中央信标时隙信息字段, 0, ts_hdr.非中央信标时隙信息字段.Length);
                idx += ts_hdr.非中央信标时隙信息字段.Length;
                ts_hdr.非中央信标 = new List<string>();
                
                for (int i = 0; i < ts_hdr.非中央信标时隙总数; i++)
                {
                    string ncb_info = "";
                    UInt16 tmp16 = comFunc.ToUInt16(ts_hdr.非中央信标时隙信息字段, i * 2);
                    ncb_info += "  " + comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                    ncb_info += comFunc.BitField16(tmp16, 12, 1) == 0 ? "|发现信标|" : "|代理信标|";
#if NWSM
                    if (comFunc.BitField16(tmp16, 13, 3) <= 6)
                    {
                        ncb_info += rf_bcn_type[comFunc.BitField16(tmp16, 13, 3)];
                    }
                    else
                    {
                        ncb_info += comFunc.BitField16(tmp16, 13, 3);
                    }
#else
                    ncb_info += "|保留:"+ comFunc.BitField16(tmp16, 13, 3);
#endif
                    ts_hdr.非中央信标.Add(ncb_info);
                }
            }

            //CSMA信息
            if (ts_hdr.CSMA相线个数 != 0)
            {
                ts_hdr.CSMA时隙信息字段 = new byte[ts_hdr.CSMA相线个数 * 4];
                Array.Copy(buf, idx, ts_hdr.CSMA时隙信息字段, 0, ts_hdr.CSMA时隙信息字段.Length);
                idx += ts_hdr.CSMA时隙信息字段.Length;
                csma_union.CSMA时隙 = new List<csma_info_c>();
                ts_hdr.CSMA时隙 = new List<string>();
                for (int i = 0; i < ts_hdr.CSMA相线个数; i++)
                {
                    csma_info_c csma = new csma_info_c();
                    UInt32 tmp32 = comFunc.ToUInt32(ts_hdr.CSMA时隙信息字段, i * 4);
                    csma.CSMA时隙长度ms = (double)comFunc.BitField32(tmp32, 0, 24) * 100 /1000;
                    csma.CSMA时隙相线 = comFunc.BitField32(tmp32, 24, 8);
                    csma_union.CSMA时隙.Add(csma);

                    if (simple_flag != 1)
                    {
                        string slot_info = "";
                        if (csma.CSMA时隙相线 == 1)
                        {
                            slot_info += "A相线";
                        }
                        else if (csma.CSMA时隙相线 == 2)
                        {
                            slot_info += "B相线";
                        }
                        else if (csma.CSMA时隙相线 == 3)
                        {
                            slot_info += "C相线";
                        }
                        else
                        {
                            slot_info += "未知相线";
                        }
                        slot_info += "  " + csma.CSMA时隙长度ms + "ms";
                        ts_hdr.CSMA时隙.Add(slot_info);
                    }
                }
            }
            //绑定CSMA信息
            if (ts_hdr.BCSMA相线个数 != 0)
            {
                ts_hdr.BCSMA时隙信息字段 = new byte[ts_hdr.BCSMA相线个数 * 4];
                Array.Copy(buf, idx, ts_hdr.BCSMA时隙信息字段, 0, ts_hdr.BCSMA时隙信息字段.Length);
                idx += ts_hdr.BCSMA时隙信息字段.Length;
                csma_union.BCSMA时隙 = new List<csma_info_c>();
                ts_hdr.BCSMA时隙 = new List<string>();
                for (int i = 0; i < ts_hdr.BCSMA相线个数; i++)
                {
                    csma_info_c csma = new csma_info_c();
                    UInt32 tmp32 = comFunc.ToUInt32(ts_hdr.BCSMA时隙信息字段, i * 4);
                    csma.CSMA时隙长度ms = (double)(comFunc.BitField32(tmp32, 0, 24) * 100 / 1000);
                    csma.CSMA时隙相线 = comFunc.BitField32(tmp32, 24, 8);
                    csma_union.BCSMA时隙.Add(csma);
                    if (simple_flag != 1)
                    {
                        string slot_info = "";
                        if (csma.CSMA时隙相线 == 1)
                        {
                            slot_info += "A相线";
                        }
                        else if (csma.CSMA时隙相线 == 2)
                        {
                            slot_info += "B相线";
                        }
                        else if (csma.CSMA时隙相线 == 3)
                        {
                            slot_info += "C相线";
                        }
                        else
                        {
                            slot_info += "未知相线";
                        }
                        slot_info += "--" + csma.CSMA时隙长度ms + "ms";
                        ts_hdr.BCSMA时隙.Add(slot_info);
                    }
                }
            }

            return ts_hdr;
        }

        /// <summary>
        /// 信标条目-国网时隙分配条目解析
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_ts_c_gw bcn_bitem_ts_gw(byte[] buf, int start, byte bcn_type, ref int simple_flag, ref csma_union_c_gw csma_union, out int tiem_len)
        {
            string[] rf_bcn_type = {"HPLC", "RF标准", "HPLC,该时隙结束后发送RF标准", "HPLC,该时隙结束后发送RF精简", "HPLC,CSMA时隙发送RF精简",
                                                      "HPLC,CSMA时隙发送RF标准", "HPLC,该时隙同步发送RF精简"};
            bitem_ts_c_gw ts_hdr = new bitem_ts_c_gw();
            ts_hdr.条目头 = buf[start + 0];
            ts_hdr.条目长度 = comFunc.ToUInt16(buf, start + 1);
            tiem_len = ts_hdr.条目长度;
            if (simple_flag != 1)
            {
                ts_hdr.数据 = new byte[tiem_len];
                Array.Copy(buf, start, ts_hdr.数据, 0, tiem_len);
            }
            if (ts_hdr.条目长度 < 23 || ((buf.Length - start) < ts_hdr.条目长度))
            {
                return ts_hdr;
            }

            ts_hdr.非中央信标时隙总数 = buf[start + 3];
            ts_hdr.中央信标时隙总数 = (byte)comFunc.BitField16(comFunc.ToUInt16(buf, start + 4), 0, 4);
            ts_hdr.CSMA相线个数 = (byte)comFunc.BitField16(comFunc.ToUInt16(buf, start + 4), 4, 2);
            ts_hdr.resv1 = comFunc.BitField16(comFunc.ToUInt16(buf, start + 4), 6, 10);
            ts_hdr.代理信标时隙总数 = buf[start + 6];
            ts_hdr.信标时隙长度ms = buf[start + 7];
            ts_hdr.CSMA时隙长度ms = (UInt16)((UInt16)buf[start + 8] * 10);
            ts_hdr.绑定CSMA时隙相线个数 = buf[start + 9];
            ts_hdr.绑定CSMA时隙链路标识符 = buf[start + 10];
            ts_hdr.TDMA时隙长度ms = (double)comFunc.ToUInt16(buf, start + 11) * 100 / 1000;
            ts_hdr.TDMA时隙链路标识符 = buf[start + 12];
            ts_hdr.NTB = comFunc.ToUInt32(buf, start + 13);
            ts_hdr.信标周期长度ms = (double)comFunc.ToUInt32(buf, start + 17) * 100 / 100;

            //ts_hdr.resv = comFunc.ToUInt32(buf, start + 23);
            ts_hdr.RF信标时隙长度ms = comFunc.BitField16(comFunc.ToUInt16(buf, start + 21), 0, 10);
            ts_hdr.resv2 = comFunc.BitField16(comFunc.ToUInt16(buf, start + 21), 10, 6);

           // ts_hdr.resv = comFunc.ToUInt32(buf, start + 23);



            int idx = start + 23;
            //非中央信标信息
            if (ts_hdr.非中央信标时隙总数 != 0 && bcn_type != 0)
            {
                ts_hdr.非中央信标时隙信息字段 = new byte[ts_hdr.非中央信标时隙总数 * 2];
                Array.Copy(buf, idx, ts_hdr.非中央信标时隙信息字段, 0, ts_hdr.非中央信标时隙信息字段.Length);
                idx += ts_hdr.非中央信标时隙信息字段.Length;
                ts_hdr.非中央信标 = new List<string>();

                for (int i = 0; i < ts_hdr.非中央信标时隙总数; i++)
                {
                    string ncb_info = "";
                    UInt16 tmp16 = comFunc.ToUInt16(ts_hdr.非中央信标时隙信息字段, i * 2);
                    ncb_info += "  " + comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                    ncb_info += comFunc.BitField16(tmp16, 12, 1) == 0 ? "|发现信标|" : "|代理信标|";
//#if NWSM
                    if (comFunc.BitField16(tmp16, 13, 3) <= 6)
                    {
                        ncb_info += rf_bcn_type[comFunc.BitField16(tmp16, 13, 3)];
                    }
                    else
                    {
                        ncb_info += comFunc.BitField16(tmp16, 13, 3);
                    }
//#else
                    ncb_info += "|保留:"+ comFunc.BitField16(tmp16, 13, 3);
//#endif
                    ts_hdr.非中央信标.Add(ncb_info);
                }
            }

            //CSMA信息
            if (ts_hdr.CSMA相线个数 != 0)
            {
                ts_hdr.CSMA时隙信息字段 = new byte[ts_hdr.CSMA相线个数 * 4];
                Array.Copy(buf, idx, ts_hdr.CSMA时隙信息字段, 0, ts_hdr.CSMA时隙信息字段.Length);
                idx += ts_hdr.CSMA时隙信息字段.Length;
                csma_union.CSMA时隙 = new List<csma_info_c_gw>();
                ts_hdr.CSMA时隙 = new List<string>();
                for (int i = 0; i < ts_hdr.CSMA相线个数; i++)
                {
                    csma_info_c_gw csma = new csma_info_c_gw();
                    UInt32 tmp32 = comFunc.ToUInt32(ts_hdr.CSMA时隙信息字段, i * 4);
                    csma.CSMA时隙长度ms = (double)comFunc.BitField32(tmp32, 0, 24) * 100 / 1000;
                    csma.CSMA时隙相线 = comFunc.BitField32(tmp32, 24, 8);
                    csma_union.CSMA时隙.Add(csma);

                    if (simple_flag != 1)
                    {
                        string slot_info = "";
                        if (csma.CSMA时隙相线 == 1)
                        {
                            slot_info += "A相线";
                        }
                        else if (csma.CSMA时隙相线 == 2)
                        {
                            slot_info += "B相线";
                        }
                        else if (csma.CSMA时隙相线 == 3)
                        {
                            slot_info += "C相线";
                        }
                        else
                        {
                            slot_info += "未知相线";
                        }
                        slot_info += "  " + csma.CSMA时隙长度ms + "ms";
                        ts_hdr.CSMA时隙.Add(slot_info);
                    }
                }
            }
            //绑定CSMA信息
            if (ts_hdr.绑定CSMA时隙相线个数 != 0)
            {
                ts_hdr.BCSMA时隙信息字段 = new byte[ts_hdr.绑定CSMA时隙相线个数 * 4];
                Array.Copy(buf, idx, ts_hdr.BCSMA时隙信息字段, 0, ts_hdr.BCSMA时隙信息字段.Length);
                idx += ts_hdr.BCSMA时隙信息字段.Length;
                csma_union.BCSMA时隙 = new List<csma_info_c_gw>();
                ts_hdr.BCSMA时隙 = new List<string>();
                for (int i = 0; i < ts_hdr.绑定CSMA时隙相线个数; i++)
                {
                    csma_info_c_gw csma = new csma_info_c_gw();
                    UInt32 tmp32 = comFunc.ToUInt32(ts_hdr.BCSMA时隙信息字段, i * 4);
                    csma.CSMA时隙长度ms = (double)(comFunc.BitField32(tmp32, 0, 24) * 100 / 1000);
                    csma.CSMA时隙相线 = comFunc.BitField32(tmp32, 24, 8);
                    csma_union.BCSMA时隙.Add(csma);
                    if (simple_flag != 1)
                    {
                        string slot_info = "";
                        if (csma.CSMA时隙相线 == 1)
                        {
                            slot_info += "A相线";
                        }
                        else if (csma.CSMA时隙相线 == 2)
                        {
                            slot_info += "B相线";
                        }
                        else if (csma.CSMA时隙相线 == 3)
                        {
                            slot_info += "C相线";
                        }
                        else
                        {
                            slot_info += "未知相线";
                        }
                        slot_info += "--" + csma.CSMA时隙长度ms + "ms";
                        ts_hdr.BCSMA时隙.Add(slot_info);
                    }
                }
            }

            return ts_hdr;
        }



        /// <summary>
        /// 信标条目-路由参数条目解析
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_rt_params_c bcn_rt_params(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_rt_params_c rt_params = new bitem_rt_params_c();
            rt_params.条目头 = buf[start + 0];
            rt_params.条目长度 = buf[start + 1];
            tiem_len = rt_params.条目长度;

            if (simple_flag != 1)
            {
                rt_params.数据 = new byte[rt_params.条目长度];
                Array.Copy(buf, start, rt_params.数据, 0, rt_params.条目长度);
            }

            if (rt_params.条目长度 != 0x22 || ((buf.Length - start) < rt_params.条目长度)) //固定值0x22
            {
                return rt_params;
            }

            rt_params.路由周期s = comFunc.ToUInt16(buf, start + 2);
            rt_params.路由评估剩余时间s = comFunc.ToUInt16(buf, start + 6);
            Array.Copy(buf, start + 28, rt_params.CCO_MAC, 0, 6);
            return rt_params;
        }

        /// <summary>
        /// 信标条目-国网路由参数条目解析
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_rt_params_c_gw bcn_rt_params_gw(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_rt_params_c_gw rt_params = new bitem_rt_params_c_gw();
            rt_params.条目头 = buf[start + 0];
            rt_params.条目长度 = buf[start + 1];
            tiem_len = rt_params.条目长度;

            if (simple_flag != 1)
            {
                rt_params.数据 = new byte[rt_params.条目长度];
                Array.Copy(buf, start, rt_params.数据, 0, rt_params.条目长度);
            }

            if (rt_params.条目长度 != 0x0A || ((buf.Length - start) < rt_params.条目长度)) //固定值0x22
            {
                return rt_params;
            }

            rt_params.路由周期s = comFunc.ToUInt16(buf, start + 2);
            rt_params.路由评估剩余时间s = comFunc.ToUInt16(buf, start + 4);
            rt_params.代理站点发现列表周期s = comFunc.ToUInt16(buf, start + 6);
            rt_params.发现站点发现列表周期s = comFunc.ToUInt16(buf, start + 8);
           // Array.Copy(buf, start + 28, rt_params.CCO_MAC, 0, 6);
            return rt_params;
        }

        /// <summary>
        /// 信标条目-频段变更解析
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_chg_band_c bcn_chg_band(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_chg_band_c chg_band = new bitem_chg_band_c();
            chg_band.条目头 = buf[start + 0];
            chg_band.条目长度 = buf[start + 1];
            tiem_len = chg_band.条目长度;
            if (simple_flag != 1)
            {
                chg_band.数据 = new byte[tiem_len];
                Array.Copy(buf, start, chg_band.数据, 0, tiem_len);
            }

            if (chg_band.条目长度 != 0x07 || ((buf.Length - start) < chg_band.条目长度)) //固定长度0x07
            {
                return chg_band;
            }

            chg_band.目标频段 = buf[start + 2];
            chg_band.频段切换剩余时间ms = comFunc.ToUInt32(buf, start + 3);
            
            return chg_band;
        }

        /// <summary>
        /// 信标条目-国网频段变更解析
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_chg_band_c_gw bcn_chg_band_gw(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_chg_band_c_gw chg_band = new bitem_chg_band_c_gw();
            chg_band.条目头 = buf[start + 0];
            chg_band.条目长度 = buf[start + 1];
            tiem_len = chg_band.条目长度;
            if (simple_flag != 1)
            {
                chg_band.数据 = new byte[tiem_len];
                Array.Copy(buf, start, chg_band.数据, 0, tiem_len);
            }

            if (chg_band.条目长度 != 0x07 || ((buf.Length - start) < chg_band.条目长度)) //固定长度0x07
            {
                return chg_band;
            }

            chg_band.目标频段 = buf[start + 2];
            chg_band.频段切换剩余时间ms = comFunc.ToUInt32(buf, start + 3);

            return chg_band;
        }

        /// <summary>
        /// 信标条目-无线路由参数条目解析
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_rfrt_params_c bcn_rfrt_params(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_rfrt_params_c rfrt_params = new bitem_rfrt_params_c();
            rfrt_params.条目头 = buf[start + 0];
            rfrt_params.条目长度 = buf[start + 1];
            tiem_len = rfrt_params.条目长度;

            if (simple_flag != 1)
            {
                rfrt_params.数据 = new byte[rfrt_params.条目长度];
                Array.Copy(buf, start, rfrt_params.数据, 0, rfrt_params.条目长度);
            }

            if (rfrt_params.条目长度 != 0x04 || ((buf.Length - start) < rfrt_params.条目长度)) //固定值0x04
            {
                return rfrt_params;
            }

            rfrt_params.无线发现列表周期s = buf[start + 2];
            rfrt_params.无线接收率老化周期个数 = buf[start + 3];
            return rfrt_params;
        }

        /// <summary>
        /// 信标条目-国网无线路由参数条目解析
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_rfrt_params_c_gw bcn_rfrt_params_gw(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_rfrt_params_c_gw rfrt_params = new bitem_rfrt_params_c_gw();
            rfrt_params.条目头 = buf[start + 0];
            rfrt_params.条目长度 = buf[start + 1];
            tiem_len = rfrt_params.条目长度;

            if (simple_flag != 1)
            {
                rfrt_params.数据 = new byte[rfrt_params.条目长度];
                Array.Copy(buf, start, rfrt_params.数据, 0, rfrt_params.条目长度);
            }

            if (rfrt_params.条目长度 != 0x04 || ((buf.Length - start) < rfrt_params.条目长度)) //固定值0x04
            {
                return rfrt_params;
            }

            rfrt_params.无线发现列表周期s = buf[start + 2];
            rfrt_params.无线接收率老化周期个数 = buf[start + 3];
            return rfrt_params;
        }

        /// <summary>
        /// 信标条目-频段探测
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_band_detect_c bcn_band_detect(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_band_detect_c band_detect = new bitem_band_detect_c();
            band_detect.条目头 = buf[start + 0];
            band_detect.条目长度 = buf[start + 1];

            tiem_len = band_detect.条目长度;

            if (simple_flag == 0)
            {
                band_detect.数据 = new byte[tiem_len];
                Array.Copy(buf, start, band_detect.数据, 0, tiem_len);
            }
            if (band_detect.条目长度 != 0x04 || ((buf.Length - start) < band_detect.条目长度))
            {
                return band_detect;
            }

            band_detect.目标频段 = buf[start + 2];
            band_detect.lid = buf[start + 3];

            return band_detect;
        }

        /// <summary>
        /// 信标条目-无线信道变更条目
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_rf_chnl_chg_c bcn_rf_chnl_chg(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_rf_chnl_chg_c rf_chnl_chg = new bitem_rf_chnl_chg_c();
            rf_chnl_chg.条目头 = buf[start + 0];
            rf_chnl_chg.条目长度 = buf[start + 1];

            tiem_len = rf_chnl_chg.条目长度;

            if (simple_flag == 0)
            {
                rf_chnl_chg.数据 = new byte[tiem_len];
                Array.Copy(buf, start, rf_chnl_chg.数据, 0, tiem_len);
            }
            if (rf_chnl_chg.条目长度 != 0x08 || ((buf.Length - start) < rf_chnl_chg.条目长度))
            {
                return rf_chnl_chg;
            }

            rf_chnl_chg.目标信道编号 = buf[start + 2];
            rf_chnl_chg.目标信道option = comFunc.BitField8(buf[start + 3], 0, 2);
            rf_chnl_chg.resv = comFunc.BitField8(buf[start + 3], 2, 6);
            rf_chnl_chg.信道切换剩余时间s = comFunc.ToUInt32(buf, start + 4) / 1000;
            return rf_chnl_chg;
        }

        /// <summary>
        /// 信标条目-国网无线信道变更条目
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_rf_chnl_chg_c_gw bcn_rf_chnl_chg_gw(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_rf_chnl_chg_c_gw rf_chnl_chg = new bitem_rf_chnl_chg_c_gw();
            rf_chnl_chg.条目头 = buf[start + 0];
            rf_chnl_chg.条目长度 = buf[start + 1];

            tiem_len = rf_chnl_chg.条目长度;

            if (simple_flag == 0)
            {
                rf_chnl_chg.数据 = new byte[tiem_len];
                Array.Copy(buf, start, rf_chnl_chg.数据, 0, tiem_len);
            }
            if (rf_chnl_chg.条目长度 != 0x08 || ((buf.Length - start) < rf_chnl_chg.条目长度))
            {
                return rf_chnl_chg;
            }

            //rf_chnl_chg.目标信道编号 = buf[start + 2];
            //rf_chnl_chg.目标信道option = comFunc.BitField8(buf[start + 3], 0, 2);
            //rf_chnl_chg.resv = comFunc.BitField8(buf[start + 3], 2, 6);
            rf_chnl_chg.目标信道 = comFunc.BitField8(buf[start + 2], 0, 8);
            rf_chnl_chg.信道切换剩余时间s = comFunc.ToUInt32(buf, start + 3) / 1000;
            return rf_chnl_chg;
        }


        /// <summary>
        /// 信标条目-万年历同步条目
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_calendar_c bcn_calendar(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_calendar_c calendar = new bitem_calendar_c();
            UInt32 second = 0;
            calendar.条目头 = buf[start + 0];
            calendar.条目长度 = buf[start + 1];
            tiem_len = calendar.条目长度;
            if (simple_flag == 0)
            {
                calendar.数据 = new byte[tiem_len];
                Array.Copy(buf, start, calendar.数据, 0, tiem_len);
            }

            if (calendar.条目长度 != 0x0A || ((buf.Length - start) < calendar.条目长度)) //固定长度0x0A
            {
                return calendar;
            }


            second = comFunc.ToUInt32(buf, start + 2);
            calendar.CCO_万年历 = ConvertToFormattedTime(second);
            calendar.CCO_万年历NTB = comFunc.ToUInt32(buf, start + 6);
            
            return calendar;
        }


        /// 信标条目-组时隙分配条目
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_zuslot_c bcn_zslot(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_zuslot_c zslot = new bitem_zuslot_c();
            zslot.条目头 = buf[start + 0];
            zslot.条目长度 = comFunc.ToUInt16(buf, start + 1);
            tiem_len = zslot.条目长度;

            if (zslot.条目长度 < 11 || (buf.Length - start) < zslot.条目长度)
            {
                return zslot;
            }

            if (simple_flag == 0)
            {
                return zslot;
            }
            zslot.数据 = new byte[tiem_len];
            Array.Copy(buf, start, zslot.数据, 0, tiem_len);
            zslot.时隙开始位置us = comFunc.ToUInt32(buf, start + 3);
            zslot.时隙长度us = comFunc.ToUInt32(buf, start + 7);

            byte index = 0;
            string tei = "";
            zslot.TEI = new List<string>();
            for (UInt16 i = 0; i < (zslot.数据.Length-11/2); i++)
            {
                tei += comFunc.ToUInt16(buf, start + 7 + i * 2).ToString("X4");
                index++;

                if (index >= 5)
                {
                    zslot.TEI.Add(tei);
                    tei = "";
                    index = 0;
                }
            }

            if (index != 0)
            {
                zslot.TEI.Add(tei);
            }

            return zslot;
        }


        /// <summary>
        /// 信标条目-分钟采集配置条目
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_mincltcfg_c bcn_mincltcfg(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_mincltcfg_c mincltcfg = new bitem_mincltcfg_c();
            mincltcfg.条目头 = buf[start + 0];
            mincltcfg.条目长度 = buf[start + 1];
            tiem_len = mincltcfg.条目长度;
            if (simple_flag == 0)
            {
                mincltcfg.数据 = new byte[tiem_len];
                Array.Copy(buf, start, mincltcfg.数据, 0, tiem_len);
            }

            if (mincltcfg.条目长度 != 0x03 || ((buf.Length - start) < mincltcfg.条目长度)) //固定长度0x0A
            {
                return mincltcfg;
            }

            mincltcfg.分钟采集开关 = comFunc.BitField8(buf[start + 2], 0, 1);
            mincltcfg.分钟采集上报周期min = comFunc.BitField8(buf[start + 2], 1, 4);
            mincltcfg.resv = comFunc.BitField8(buf[start + 2], 5, 3);

            return mincltcfg;
        }


        /// <summary>
        /// 信标条目-友讯达设置私有参数条目
        /// </summary>
        /// <param name="buf"></param>
        /// <returns></returns>
        public static bitem_yxd_private_c bcn_bitem_yxd_private(byte[] buf, int start, ref int simple_flag, out int tiem_len)
        {
            bitem_yxd_private_c yxd_private = new bitem_yxd_private_c();
            yxd_private.条目头 = buf[start + 0];
            yxd_private.条目长度 = buf[start + 1];
            tiem_len = yxd_private.条目长度;
            if (simple_flag == 0)
            {
                yxd_private.数据 = new byte[tiem_len];
                Array.Copy(buf, start, yxd_private.数据, 0, tiem_len);
            }

            if (yxd_private.条目长度 != 16 || ((buf.Length - start) < yxd_private.条目长度)) //固定长度0x0A
            {
                return yxd_private;
            }

            yxd_private.sta工作模式 = buf[start + 2];      /*!< 0:自适应模式 1：固定HPLC单模 其他值表示保留原来配置 */
            yxd_private.sta载波功率 = (sbyte)buf[start + 3];     /*!< 范围-4到10 其他值表示保留原来配置 */
            yxd_private.sta无线功率 = (sbyte)buf[start + 4];     /*!< 范围30到48 其他值表示保留原来配置 */
            yxd_private.PCO发送发现列表周期 = buf[start + 5];      /*!< 设置范围：5-20次每个路由周期 其他值表示保留原来配置 */
            yxd_private.STA发送发现列表周期 = buf[start + 6];      /*!< 设置范围：5-20次每个路由周期 其他值表示保留原来配置 */
            yxd_private.STA周期代理变更间隔 = buf[start + 7];      /*!< 设置范围：1-8小时 其他值表示保留原来配置 */
            yxd_private.STA心跳报文发送周期 = buf[start + 8];      /*!< 设置范围：1-8分之一个路由周期 其他值表示保留原来配置 */
            Array.Copy(buf, 9, yxd_private.resv, 0, 6);
            yxd_private.crc8 = buf[start + 15];

            return yxd_private;
        }

        static string ConvertToFormattedTime(long secondsSinceEpoch)
        {
            // 2000年1月1日00:00:00的DateTime对象  
            DateTime epoch = new DateTime(2000, 1, 1, 0, 0, 0, DateTimeKind.Utc);

            // 将秒数转换为TimeSpan对象  
            TimeSpan timeSpan = TimeSpan.FromSeconds(secondsSinceEpoch);

            // 将epoch与TimeSpan相加得到实际时间  
            DateTime actualTime = epoch.Add(timeSpan);

            // 按照年-月-日 时:分:秒的形式格式化输出字符串  
            string formattedDateTime = actualTime.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);

            return formattedDateTime;
        }




        /// <summary>
        /// 信标类型解析
        /// </summary>
        /// <param name="btype"></param>
        /// <returns></returns>
        public static string bcn_type(byte btype)
        {
            ///0: 发现信标; 1: 代理信标; 2:中央信标
            string bt = "未知信标";
            switch (btype)
            {
                case 0:
                    bt = "发现信标";
                    break;
                case 1:
                    bt = "代理信标";
                    break;
                case 2:
                    bt = "中央信标";
                    break;
                default:
                    break;
            }
            return bt;
        }

        /// <summary>
        /// MAC帧解析-SOF-单跳帧头
        /// </summary>
        /// <param name="buf"></param>
        /// <param name="bpsz"></param>
        /// <returns></returns>
        public static mac_hdr_single_t msdu_sof_mac_hdr_single(byte[] buf, int bpsz, ref int simple_flag)
        {
            mac_hdr_single_t mhdr_s = new mac_hdr_single_t();
            if (simple_flag != 1)
            {
                Array.Copy(buf, 0, mhdr_s.原始数据, 0, 4);
            }

            mhdr_s.帧头类型 = comFunc.BitField8(buf[0], 0, 1);
            mhdr_s.版本 = comFunc.BitField8(buf[0], 1, 2);
            mhdr_s.resv = comFunc.BitField8(buf[0], 3, 5);
            mhdr_s.MSDU类型 = buf[1];
            mhdr_s.MSDU长度 = comFunc.ToUInt16(buf, 2);

            return mhdr_s;
        }

        /// <summary>
        /// MAC帧解析-SOF-国网单跳帧头
        /// </summary>
        /// <param name="buf"></param>
        /// <param name="bpsz"></param>
        /// <returns></returns>
        public static mac_hdr_single_t_gw msdu_sof_mac_hdr_single_gw(byte[] buf, int bpsz, ref int simple_flag)
        {
            UInt16 tmp16 = 0;
            mac_hdr_single_t_gw mhdr_s_gw = new mac_hdr_single_t_gw();
            if (simple_flag != 1)
            {
                Array.Copy(buf, 0, mhdr_s_gw.原始数据, 0, 4);
            }

            mhdr_s_gw.版本 = comFunc.BitField8(buf[0], 0, 4);
            mhdr_s_gw.resv1 = comFunc.BitField8(buf[0], 4, 4);
            mhdr_s_gw.消息类型 = comFunc.BitField8(buf[1], 0, 8);
            tmp16 = comFunc.ToUInt16(buf, 2);
            mhdr_s_gw.MSDU长度 = comFunc.BitField16(tmp16, 0, 11);
            mhdr_s_gw.resv2 = comFunc.BitField16(tmp16, 11, 5);

            return mhdr_s_gw;
        }

        /// <summary>
        /// MAC帧解析-SOF-长帧头
        /// </summary>
        /// <param name="buf"></param>
        /// <param name="bpsz"></param>
        /// <returns></returns>
        public static mac_hdr_l_t msdu_sof_mac_hdr_l(byte[] buf, int bpsz, ref int simple_flag)
        {
            mac_hdr_l_t mhdr_l = new mac_hdr_l_t();
            byte tmp;
            UInt16 tmp16;
            UInt32 tmp32;

            if (simple_flag != 1)
            {
                Array.Copy(buf, 0, mhdr_l.原始数据, 0, 32);
            }

            tmp16 = comFunc.ToUInt16(buf, 0);
            mhdr_l.帧头类型 = comFunc.BitField16(tmp16, 0, 1);
            mhdr_l.协议版本 = comFunc.BitField16(tmp16, 1, 2);
            mhdr_l.resv1 = comFunc.BitField16(tmp16, 2, 13);

            mhdr_l.MSDU长度 = comFunc.ToUInt16(buf, 2);

            tmp32 = comFunc.ToUInt32(buf, 4);
            mhdr_l.原始目的TEI = comFunc.BitField32(tmp32, 0, 12).ToString("X3");
            mhdr_l.原始源TEI = comFunc.BitField32(tmp32, 12, 12).ToString("X3");
            mhdr_l.SNID = comFunc.BitField32(tmp32, 24, 4);
            mhdr_l.重启次数 = comFunc.BitField32(tmp32, 28, 4);

            if (simple_flag != 1)
            {
                mhdr_l.路由跳数 = comFunc.BitField8(buf[8], 0, 4);
                tmp = comFunc.BitField8(buf[8], 4, 4);
                if (tmp == 1)
                {
                    mhdr_l.广播方向 = "下行广播";
                }
                else if(tmp == 2)
                {
                    mhdr_l.广播方向 = "上行广播";
                }
                else
                {
                    mhdr_l.广播方向 = "保留：" + tmp.ToString();
                }
                tmp = comFunc.BitField8(buf[9], 0, 3);
                if (tmp == 0)
                {
                    mhdr_l.发送类型 = "单播，需要确认回应";
                }
                else if (tmp == 1)
                {
                    mhdr_l.发送类型 = "全网广播，不需要回应";
                }
                else if (tmp == 2)
                {
                    mhdr_l.发送类型 = "本地广播，不需要回应";
                }
                else if (tmp == 3)
                {
                    mhdr_l.发送类型 = "全网广播，需要确认回应";
                }
                else if (tmp == 4)
                {
                    mhdr_l.发送类型 = "本地广播，需要确认回应";
                }
                else
                {
                    mhdr_l.发送类型 = "保留";
                }
            }
            
            mhdr_l.发送次数限值 = comFunc.BitField8(buf[9], 3, 5);
            mhdr_l.MSDU序列号 = comFunc.ToUInt16(buf, 10).ToString("X4");

            Array.Copy(buf, 12, mhdr_l.dst_mac, 0, 6);
            mhdr_l.时间戳保留 = comFunc.ToUInt32(buf, 10);
            Array.Copy(buf, 22, mhdr_l.resv2, 0, 10);

            return mhdr_l;
        }

        /// <summary>
        /// MAC帧解析-SOF-国网标准帧头
        /// </summary>
        /// <param name="buf"></param>
        /// <param name="bpsz"></param>
        /// <returns></returns>
        public static mac_hdr_gw msdu_sof_mac_hdr_gw(byte[] buf, int bpsz, ref int simple_flag)
        {
            mac_hdr_gw mhdr_gw = new mac_hdr_gw();
            byte tmp;
            UInt16 tmp16;
           // UInt32 tmp32;
            UInt16 tmp16_2;
            UInt16 tmp16_3;

            if (simple_flag != 1)
            {
                Array.Copy(buf, 0, mhdr_gw.原始数据, 0, 28);
            }

            tmp16 = comFunc.ToUInt16(buf, 0);
            mhdr_gw.版本 = comFunc.BitField16(tmp16, 0, 4) == 0 ? "标准帧协议":"单跳帧协议";
            mhdr_gw.原始源TEI = comFunc.BitField16(tmp16, 4, 12).ToString("X3");
            tmp16_2 = comFunc.ToUInt16(buf, 2);
            mhdr_gw.原始目的TEI = comFunc.BitField16(tmp16_2, 0, 12).ToString("X3");
            //mhdr_gw.发送类型 = comFunc.BitField16(tmp16_2, 12, 1);
            mhdr_gw.发送次数限值 = comFunc.BitField8(buf[4], 0, 4);
            mhdr_gw.resv1 = comFunc.BitField8(buf[4], 4, 4);
            mhdr_gw.MSDU序列号 = comFunc.ToUInt16(buf, 5).ToString("X4");
            
            if (comFunc.BitField8(buf[7], 0, 8) == 0)
            {
                mhdr_gw.MSDU类型 = "网络消息管理报文";
            }
            else if (( comFunc.BitField8(buf[7], 0, 8) < 48) && (0 < comFunc.BitField8(buf[7], 0, 8)))
            {
                mhdr_gw.MSDU类型 = "数据链路层待扩展";
            }
            else if (comFunc.BitField8(buf[7], 0, 8) == 48) 
            {
                mhdr_gw.MSDU类型 = "应用层报文";
            }
            else if (comFunc.BitField8(buf[7], 0, 8) == 49)
            {
                mhdr_gw.MSDU类型 = "IP报文";
            }
            else
            {
                mhdr_gw.MSDU类型 = "待扩展";
            }

            tmp16_3 = comFunc.ToUInt16(buf, 8);
            mhdr_gw.MSDU长度 = comFunc.BitField16(tmp16_3, 0, 11);
            mhdr_gw.重启次数 = comFunc.BitField16(tmp16_3, 11, 4);
            mhdr_gw.MianFlag = comFunc.BitField16(tmp16_3, 15, 1);
            mhdr_gw.路由跳数 = comFunc.BitField8(buf[10], 0, 4);
            mhdr_gw.路由剩余跳数 = comFunc.BitField8(buf[10], 4, 4);
           // mhdr_gw.广播方向 = comFunc.BitField8(buf[11], 0, 2);

            tmp = comFunc.BitField8(buf[11], 0, 2);
            if (tmp == 0)
            {
                mhdr_gw.广播方向 = "双向广播";
            }
            else if (tmp == 1)
            {
                mhdr_gw.广播方向 = "下行广播（CCO->STA）";
            }
            else if (tmp == 2)
            {
                mhdr_gw.广播方向 = "上行广播（STA->CCO）";
            }
            else
            {
                mhdr_gw.广播方向 = "保留：" + tmp.ToString();
            }
            tmp = (byte)comFunc.BitField16(tmp16_2, 12, 4);
            if (tmp == 0)
            {
                mhdr_gw.发送类型 = "单播";
            }
            else if (tmp == 1)
            {
                mhdr_gw.发送类型 = "全网广播";
            }
            else if (tmp == 2)
            {
                mhdr_gw.发送类型 = "本地广播";
            }
            else if (tmp == 3)
            {
                mhdr_gw.发送类型 = "代理广播";
            }
            else
            {
                mhdr_gw.发送类型 = "保留";
            }

            mhdr_gw.路径修复标志 = comFunc.BitField8(buf[11], 2, 1);
            mhdr_gw.mac地址标志 = comFunc.BitField8(buf[11], 3, 1);
            mhdr_gw.resv2 = comFunc.BitField8(buf[11], 4, 4);
            mhdr_gw.resv3 = comFunc.BitField8(buf[12], 0, 8);
            mhdr_gw.组网序列号 = comFunc.BitField8(buf[13], 0, 8);
            mhdr_gw.resv4 = comFunc.BitField8(buf[14], 0, 8);
            mhdr_gw.resv5 = comFunc.BitField8(buf[15], 0, 8);
            if(mhdr_gw.mac地址标志 == 0)
            {
                mhdr_gw.源mac地址 = null;
                mhdr_gw.目的mac地址 = null;
            }
            else
            {
                Array.Copy(buf, 16, mhdr_gw.源mac地址, 0, 6);
                Array.Copy(buf, 22, mhdr_gw.目的mac地址, 0, 6);
            }
            


            return mhdr_gw;
        }

        /// <summary>
        /// MAC帧解析-SOF-短帧头
        /// </summary>
        /// <param name="buf"></param>
        /// <param name="bpsz"></param>
        /// <returns></returns>
        public static mac_hdr_s_t msdu_sof_mac_hdr_s(byte[] buf, int bpsz, ref int simple_flag)
        {
            mac_hdr_s_t mhdr_s = new mac_hdr_s_t();
            byte tmp;
            UInt16 tmp16;
            UInt32 tmp32;

            if (simple_flag != 1)
            {
                Array.Copy(buf, 0, mhdr_s.原始数据, 0, 12);
            }

            tmp16 = comFunc.ToUInt16(buf, 0);
            mhdr_s.帧头类型 = comFunc.BitField16(tmp16, 0, 1);
            mhdr_s.版本 = comFunc.BitField16(tmp16, 1, 2);
            mhdr_s.resv = comFunc.BitField16(tmp16, 3, 13);

            mhdr_s.MSDU长度 = comFunc.ToUInt16(buf, 2);

            tmp32 = comFunc.ToUInt32(buf, 4);
            mhdr_s.原始目的TEI = comFunc.BitField32(tmp32, 0, 12).ToString("X3");
            mhdr_s.原始源TEI = comFunc.BitField32(tmp32, 12, 12).ToString("X3");
            mhdr_s.SNID = comFunc.BitField32(tmp32, 24, 4);
            mhdr_s.重启次数 = comFunc.BitField32(tmp32, 28, 4);

            if (simple_flag != 1)
            {
                mhdr_s.路由跳数 = comFunc.BitField8(buf[8], 0, 4);
                tmp = comFunc.BitField8(buf[8], 4, 4);
                if (tmp == 1)
                {
                    mhdr_s.广播方向 = "下行广播";
                }
                else if (tmp == 2)
                {
                    mhdr_s.广播方向 = "上行广播";
                }
                else
                {
                    mhdr_s.广播方向 = "保留：" + tmp.ToString();
                }
                tmp = comFunc.BitField8(buf[9], 0, 3);
                if (tmp == 0)
                {
                    mhdr_s.发送类型 = "单播，需要确认回应";
                }
                else if (tmp == 1)
                {
                    mhdr_s.发送类型 = "全网广播，不需要回应";
                }
                else if (tmp == 2)
                {
                    mhdr_s.发送类型 = "本地广播，不需要回应";
                }
                else if (tmp == 3)
                {
                    mhdr_s.发送类型 = "全网广播，需要确认回应";
                }
                else if (tmp == 4)
                {
                    mhdr_s.发送类型 = "本地广播，需要确认回应";
                }
                else
                {
                    mhdr_s.发送类型 = "保留";
                }
            }
            mhdr_s.发送次数限值 = comFunc.BitField8(buf[9], 3, 5);
            mhdr_s.MSDU序列号 = comFunc.ToUInt16(buf, 10).ToString("X4");
            mhdr_s.VLAN标签 = buf[12];
            mhdr_s.MSDU类型 = buf[13];
            return mhdr_s;
        }

        //长帧头解析，即网络管理消息
        public static sof_pld_c msdu_sof_single(byte[] buf, int bpsz, ref int simple_flag, ref string detail)
        {
            sof_pld_c sof_pld = new sof_pld_c();

            sof_pld.MAC单跳帧头 = msdu_sof_mac_hdr_single(buf, bpsz, ref simple_flag);

            if (bpsz < (sof_pld.MAC单跳帧头.MSDU长度 + 4))
            {
                return sof_pld;
            }

            if (sof_pld.MAC单跳帧头.MSDU类型 == 1) //应用层报文
            {
                sof_pld.应用层 = aps_mng_deal(buf, bpsz, 4, ref simple_flag, ref detail);
            }
            else if (sof_pld.MAC单跳帧头.MSDU类型 == 2) //无线发现列表消息
            {
                sof_pld.单跳帧 = (Object)rfDiscoverList_deal(buf, bpsz, 4, ref simple_flag, ref detail);
            }
            else
            {

            }
            
            return sof_pld;
        }

        //国网单跳帧解析
        public static sof_pld_c msdu_sof_single_gw(byte[] buf, int bpsz, ref int simple_flag, ref string detail)
        {
            sof_pld_c sof_pld = new sof_pld_c();

            sof_pld.国网MAC单跳帧头 = msdu_sof_mac_hdr_single_gw(buf, bpsz, ref simple_flag);

            if (bpsz < (sof_pld.国网MAC单跳帧头.MSDU长度 + 4))
            {
                return sof_pld;
            }

            if (sof_pld.国网MAC单跳帧头.消息类型 == 128) //应用层报文
            {
                sof_pld.GW应用层 = aps_mng_deal_gw(buf, bpsz, 4, ref simple_flag, ref detail);
            }
            else if (sof_pld.国网MAC单跳帧头.消息类型 == 0) //无线发现列表消息
            {
                sof_pld.单跳帧 = (Object)rfDiscoverList_deal(buf, bpsz, 4, ref simple_flag, ref detail);
            }
            else
            {

            }

            return sof_pld;
        }


        //长帧头解析，即网络管理消息
        public static sof_pld_c msdu_sof_l(byte[] buf, int bpsz, ref int simple_flag, ref string detail)
        {
            sof_pld_c sof_pld = new sof_pld_c();

            sof_pld.MAC长帧头 = msdu_sof_mac_hdr_l(buf, bpsz, ref simple_flag);

            sof_pld.MAC长帧头.MSDU长帧头 = nwk_hdr_deal(buf, 32, ref simple_flag);

            sof_pld.MME = nwk_mng_deal(buf, bpsz, 32 + 24, (UInt16)sof_pld.MAC长帧头.MSDU长帧头.管理消息类型, ref simple_flag, ref detail);
            return sof_pld;
        }

        //国网标准帧头解析
        public static sof_pld_c msdu_sof_gw(byte[] buf, int bpsz, ref int simple_flag, ref string detail)
        {
            UInt16 start = 16;
            sof_pld_c sof_pld = new sof_pld_c();

            sof_pld.GW标准帧 = msdu_sof_mac_hdr_gw(buf, bpsz, ref simple_flag);

            if (sof_pld.GW标准帧.mac地址标志 == 1)
            {
                start = 28;
            }

            sof_pld.GW标准帧.MSDU标准帧 = nwk_hdr_deal_gw(buf, start, ref simple_flag);

            if (sof_pld.GW标准帧.MSDU类型 == "网络消息管理报文")
            {
                sof_pld.MME = nwk_mng_deal_gw(buf, bpsz, start + 4, (UInt16)sof_pld.GW标准帧.MSDU标准帧.管理消息类型, ref simple_flag, ref detail);
            }
            
            else if (sof_pld.GW标准帧.MSDU类型 == "应用层报文")
            {
                sof_pld.GW应用层 = aps_mng_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
            }
            return sof_pld;
        }

        public static void msdu_sof_l_para_extract(ref sof_pld_c sof_pld, ref string frame_type, ref string addr)
        {
            switch ((UInt16)sof_pld.MAC长帧头.MSDU长帧头.管理消息类型)
            {
                case 0x0030:    //关联请求
                    MMAssocReq_c assocReq = (MMAssocReq_c)sof_pld.MME.帧荷载信息;
                    addr = comFunc.ByteArryToHexStr_2(assocReq.站点MAC地址);
                    break;

                case 0x0031:    //关联确认
                    //addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始目的地址);
                    MMeAssocCnf_c assocCnf = (MMeAssocCnf_c)sof_pld.MME.帧荷载信息;
                    addr = comFunc.ByteArryToHexStr_2(assocCnf.站点MAC地址);
                    break;

                case 0x0032:    //代理变更请求
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始源地址);
                    break;

                case 0x0034:    //关联指示
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始目的地址);
                    //MMeAssocInd_s assocInd = (MMeAssocInd_s)sof_pld.MME.帧荷载信息;
                    //addr = comFunc.ByteArryToHexStr_2(assocInd.站点MAC地址);
                    break;

                case 0x0037:    //代理变更确认
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始目的地址);
                    break;

                case 0x003A:    //关联汇总指示
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始目的地址);
                    break;

                case 0x003B:    //代理变更确认位图版
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始目的地址);
                    break;

                case 0x0049:    //离线指示
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始目的地址);
                    //MMeLeaveInd_s leaveInd = (MMeLeaveInd_s)sof_pld.MME.帧荷载信息;
                    //addr = comFunc.ByteArryToHexStr_2(leaveInd.站点MAC地址);
                    break;

                case 0x0051:    //心跳检测
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始源地址);
                    break;

                case 0x0055:    //发现列表
                    //MMeDiscoverNodeList_s discoverNodeList = (MMeDiscoverNodeList_s)sof_pld.MME.帧荷载信息;
                    //addr = comFunc.ByteArryToHexStr_2(discoverNodeList.MAC地址);
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始源地址);
                    break;

                case 0x005D:    //延迟离线指示
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始目的地址);
                    break;

                case 0x005E:    //通信成功率上报
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始源地址);
                    break;

                case 0x005F:    //网络冲突上报
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始源地址);
                    break;

                case 0x0062:    //过零NTB采集指示
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始目的地址);
                    break;

                case 0x0063:    //过零NTB上报
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始源地址);
                    break;

                case 0x0064:    //网络诊断报文
                    break;

                case 0x0070:    //无线信道冲突上报
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始源地址);
                    break;
                case 0x00A0:    //采集汇聚数据上报
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始源地址);
                    break;
                default:
                    break;
            }
            UInt16 msg_type = (UInt16)sof_pld.MAC长帧头.MSDU长帧头.管理消息类型;
            sof_pld.MAC长帧头.MSDU长帧头.管理消息类型 = (Object)(msg_type.ToString("X4"));
            frame_type = sof_pld.MME.帧类型;
        }

        /*国网消息管理解析*/
        public static void msdu_sof_l_para_extract_gw(ref sof_pld_c sof_pld, ref string frame_type, ref string addr)
        {
            switch ((UInt16)sof_pld.GW标准帧.MSDU标准帧.管理消息类型)
            {
                case 0x0000:    //关联请求
                    MMAssocReq_c_gw assocReq = (MMAssocReq_c_gw)sof_pld.MME.帧荷载信息;
                    addr = comFunc.ByteArryToHexStr_2(assocReq.站点MAC地址);
                    break;

                case 0x0001:    //关联确认
                    //addr = comFunc.ByteArryToHexStr_2(sof_pld.MAC长帧头.MSDU长帧头.原始目的地址);
                    MMeAssocCnf_c_gw assocCnf = (MMeAssocCnf_c_gw)sof_pld.MME.帧荷载信息;
                    addr = comFunc.ByteArryToHexStr_2(assocCnf.站点MAC地址);
                    break;

                case 0x0002:    //关联汇总指示
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x0003:    //代理变更请求
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x0004:    //代理变更确认
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x0005:    //代理变更确认位图版
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x0006:    //离线指示
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    sta_mac_addr_c leaveInd = (sta_mac_addr_c)sof_pld.MME.帧荷载信息;
                    addr = comFunc.ByteArryToHexStr_2(leaveInd.MAC);
                    break;

                case 0x0007:    //心跳检测
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x0008:    //发现列表
                    MMeDiscoverNodeList_s_gw discoverNodeList = (MMeDiscoverNodeList_s_gw)sof_pld.MME.帧荷载信息;
                    addr = comFunc.ByteArryToHexStr_2(discoverNodeList.MAC地址);
                    //addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x0009:    //通信成功率上报
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x000A:    //网络冲突上报
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x000B:    //过零NTB采集指示
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x000C:    //过零NTB上报
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x004F:    //网络诊断报文
                    break;

                case 0x0050:    //路由请求
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x0051:    //路由回复
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x0052:    //路由错误
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x0053:    //路由应答
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x0054:    //链路确认请求
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                case 0x0055:    //链路确认回应
                    addr = comFunc.ByteArryToHexStr_2(sof_pld.GW标准帧.目的mac地址);
                    break;

                default:
                    break;
            }
            UInt16 msg_type = (UInt16)sof_pld.GW标准帧.MSDU标准帧.管理消息类型;
            sof_pld.GW标准帧.MSDU标准帧.管理消息类型 = (Object)(msg_type.ToString("X4"));
            frame_type = sof_pld.MME.帧类型;
        }

        public static void msdu_sof_s_para_extract(ref sof_pld_c sof_pld, ref string frame_type, ref string addr)
        {
            switch (sof_pld.应用层.具体帧类型)
            {
                case 0:    //确认帧
                    frame_type = "确认";
                    break;
                case 1:    //否认帧
                    frame_type = "否认";
                    break;
                case 2:    //设备数据传输
                    frame_type = "设备数据传输";

                    if (sof_pld.应用层.传输方向位 == 0)
                    {
                        aps_to_dev_down_c aps_to_dev_down = (aps_to_dev_down_c)sof_pld.应用层.帧荷载解析;
                        addr = comFunc.ByteArryToHexStr_2(aps_to_dev_down.目的地址);
                    }
                    else
                    {
                        aps_to_dev_up_c aps_to_dev_up = (aps_to_dev_up_c)sof_pld.应用层.帧荷载解析;
                        addr = comFunc.ByteArryToHexStr_2(aps_to_dev_up.源地址);
                    }
                    break;
                case 3: //模块数据传输
                    frame_type = "模块数据传输";
                    aps_to_mdl_c aps_to_mdl = (aps_to_mdl_c)sof_pld.应用层.帧荷载解析;
                    if (sof_pld.应用层.传输方向位 == 0)
                    {
                        addr = comFunc.ByteArryToHexStr_2(aps_to_mdl.目的地址);
                    }
                    else
                    {
                        addr = comFunc.ByteArryToHexStr_2(aps_to_mdl.源地址);
                    }
                    break;
                case 4:
                    frame_type = "查询终端搜索结果";
                    break;
                case 5:
                    frame_type = "下发搜索终端列表";
                    break;
                case 6: //下发文件信息
                    frame_type = "文件传输";
                    aps_file_trans_c aps_file_trans = (aps_file_trans_c)sof_pld.应用层.帧荷载解析;
                    if (sof_pld.应用层.传输方向位 == 0)
                    {
                        trans_info_down_c file_info = (trans_info_down_c)aps_file_trans.文件传输信息;
                        addr = comFunc.ByteArryToHexStr_2(file_info.目的地址);
                    }
                    
                    break;
                case 7: //下发文件数据
                case 8: //查询文件接收状态
                case 9: //文件传输完成通知
                    frame_type = "文件传输";
                    break;
                case 10: //从节点事件设置
                    frame_type = "从节点事件设置";
                    break;
                case 11: //从节点重启
                    frame_type = "从节点重启";
                    break;
                case 12: //从节点信息查询
                    frame_type = "从节点信息查询";
                    break;
                case 13: //下发通信地址映射列表
                    frame_type = "下发通信map";
                    break;
                case 14: //查询从节点运行状态信息
                    frame_type = "查询节点运行状态";
                    break;
                case 15: //查询从节点信道信息
                    frame_type = "查询节点信道信息";
                    break;
                case 16: //台区识别
                    frame_type = "台区识别";
                    aps_ad_phase_c aps_ad = (aps_ad_phase_c)sof_pld.应用层.帧荷载解析;
                    addr = comFunc.ByteArryToHexStr_2(aps_ad.MAC地址);
                    break;
                case 17: //台区识别
                    frame_type = "相位识别";
                    aps_ad_phase_c aps_phase = (aps_ad_phase_c)sof_pld.应用层.帧荷载解析;
                    addr = comFunc.ByteArryToHexStr_2(aps_phase.MAC地址);
                    break;
                case 18: //测试帧
                    frame_type = "测试";
                    break;
                case 19: //电表事件上报
                    frame_type = "电表事件";
                    aps_evt_rpt_up_c aps_evt_rpt_up = (aps_evt_rpt_up_c)sof_pld.应用层.帧荷载解析;
                    addr = comFunc.ByteArryToHexStr_2(aps_evt_rpt_up.电表地址);
                    break;
                case 20: //设备事件上报
                    frame_type = "设备事件";
                    aps_dev_evt_rpt_up_c dev_evt_rpt = (aps_dev_evt_rpt_up_c)sof_pld.应用层.帧荷载解析;
                    addr = comFunc.ByteArryToHexStr_2(dev_evt_rpt.设备地址);
                    break;
                case 21: //设备事件上报
                    frame_type = "停复电事件";
                    break;
                case 22: //模块事件上报
                    frame_type = "模块事件";
                    break;
                case 23: //CKQ-CCO
                    frame_type = "CKQ_CCO";
                    break;
                case 24: //CKQ-serial
                    frame_type = "CKQ_串口";
                    break;
                case 25: //广播业务
                    frame_type = "广播";
                    break;
                case 26: //数据订阅
                    frame_type = "数据订阅";
                    break;

                case 254://错误的应用帧
                    frame_type = "APS_err";
                    break;
                default:
                    frame_type = "APS";
                    break;
            }
        }



        public static void msdu_sof_s_para_extract_gw(ref sof_pld_c sof_pld, ref string frame_type, ref string addr)
        {
            switch (sof_pld.GW应用层.具体帧类型)
            {
                case 1:    //终端主动抄表
                    frame_type = "终端主动抄表";
                    break;
                case 2:    //路由主动抄表
                    frame_type = "路由主动抄表";
                    break;
                case 3:    //终端并发抄表
                    frame_type = "终端主动并发抄表";
                    break;
                case 4:    //校时
                    frame_type = "校时";
                    break;
                case 5:    //通讯测试
                    frame_type = "通讯测试";
                    break;
                case 6:    //事件上报
                    frame_type = "事件上报";
                    break;
                case 7:    //查询从节点主动注册
                    frame_type = "查询从节点主动注册";
                    break;
                case 8:    //启动从节点主动注册
                    frame_type = "启动从节点主动注册";
                    break;
                case 9:    //停止从节点主动注册
                    frame_type = "停止从节点主动注册";
                    break;
                case 10:    //确认/否认
                    frame_type = "确认/否认";
                    break;
                case 11:    //开始升级
                    frame_type = "开始升级";
                    break;
                case 12:    //停止升级
                    frame_type = "停止升级";
                    break;
                case 13:    //传输文件数据
                    frame_type = "传输文件数据";
                    break;
                case 14:    //传输文件数据（单播转本地广播）
                    frame_type = "传输文件数据（单播转本地广播）";
                    break;
                case 15:    //查询站点升级状态
                    frame_type = "查询站点升级状态";
                    break;
                case 16:    //执行升级
                    frame_type = "执行升级";
                    break;
                case 17:    //查询站点信息
                    frame_type = "查询站点信息";
                    break;
                case 18:    //抄控器CCO
                    frame_type = "抄控器CCO";
                    break;
                case 19:    //抄控器数据透传串口转发
                    frame_type = "抄控器数据透传串口转发";
                    break;
                case 20:    //鉴权安全
                    frame_type = "鉴权安全";
                    break;
                case 21:    //台区户变关系识别
                    frame_type = "台区户变关系识别";
                    break;
                case 22:    //查询ID信息
                    frame_type = "查询ID信息";
                    break;
                case 23:    //精准校时
                    frame_type = "精准校时";
                    break;
                case 24:    //配电信息上报
                    frame_type = "配电信息上报";
                    break;


                case 254://错误的应用帧
                    frame_type = "APS_err";
                    break;
                default:
                    frame_type = "APS";
                    break;
            }
        }


        //短帧头解析，即APS层
        public static sof_pld_c msdu_sof_s(byte[] buf, int bpsz, ref int simple_flag, ref string detail)
        {
            sof_pld_c sof_pld = new sof_pld_c();
            sof_pld.MAC短帧头 = msdu_sof_mac_hdr_s(buf, bpsz, ref simple_flag);
            sof_pld.应用层 = aps_mng_deal(buf, bpsz, 12 + 2, ref simple_flag, ref detail);
            return sof_pld;
        }





        //管理消息报文头解析
        public static nwk_hdr_l_c nwk_hdr_deal(byte[] buf, int start, ref int simple_flag)
        {
            nwk_hdr_l_c nwk_hdr = new nwk_hdr_l_c();
            if (simple_flag != 1)
            {
                Array.Copy(buf, start, nwk_hdr.原始数据, 0, 24);
            }

            Array.Copy(buf, start, nwk_hdr.原始目的地址, 0, 6);
            Array.Copy(buf, start + 6, nwk_hdr.原始源地址, 0, 6);
            nwk_hdr.VLAN = comFunc.ToUInt32(buf, start + 12).ToString("X4");
            nwk_hdr.MSDU帧类型 = comFunc.ToUInt16(buf, start + 16).ToString("X4");
            nwk_hdr.管理消息版本 = buf[start + 18];
            nwk_hdr.管理消息类型 = (Object)comFunc.ToUInt16(buf, start + 19);
            Array.Copy(buf, start + 21, nwk_hdr.resv, 0, 3);

            return nwk_hdr;
        }

        //国网管理消息报文头解析
        public static nwk_hdr_c_gw nwk_hdr_deal_gw(byte[] buf, int start, ref int simple_flag)
        {
            nwk_hdr_c_gw nwk_hdr = new nwk_hdr_c_gw();
            if (simple_flag != 1)
            {
                Array.Copy(buf, start, nwk_hdr.原始数据, 0, 4);
            }

            /*Array.Copy(buf, start, nwk_hdr.原始目的地址, 0, 6);
            Array.Copy(buf, start + 6, nwk_hdr.原始源地址, 0, 6);
            nwk_hdr.VLAN = comFunc.ToUInt32(buf, start + 12).ToString("X4");
            nwk_hdr.MSDU帧类型 = comFunc.ToUInt16(buf, start + 16).ToString("X4");
            nwk_hdr.管理消息版本 = buf[start + 18];*/
            nwk_hdr.管理消息类型 = (Object)comFunc.ToUInt16(buf, start + 0);
            Array.Copy(buf, start + 2, nwk_hdr.resv, 0, 2);

            return nwk_hdr;
        }

        public static nwk_mng_c nwk_mng_deal(byte[] buf, int bpsz, int start, UInt16 type, ref int simple_flag, ref string detail)
        {
            nwk_mng_c nwk_mng = new nwk_mng_c();
            switch (type)
            {
                case 0x0030:    //关联请求
                    nwk_mng.帧类型 = "关联请求";
                    nwk_mng.帧荷载信息 = (Object)assocReq_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0031:    //关联确认
                    nwk_mng.帧类型 = "关联确认";
                    nwk_mng.帧荷载信息 = (Object)assocCnf_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0032:    //代理变更请求
                    nwk_mng.帧类型 = "代理变更请求";
                    nwk_mng.帧荷载信息 = (Object)changeProxyReq_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0034:    //关联指示
                    nwk_mng.帧类型 = "关联指示";
                    nwk_mng.帧荷载信息 = (Object)assocInd_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0037:    //代理变更确认
                    nwk_mng.帧类型 = "代理变更确认";
                    nwk_mng.帧荷载信息 = (Object)changeProxyCnf_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x003A:    //关联汇总指示
                    nwk_mng.帧类型 = "关联汇总指示";
                    nwk_mng.帧荷载信息 = (Object)assocGatherInd_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x003B:    //代理变更确认位图版
                    nwk_mng.帧类型 = "代理变更确认位图版";
                    nwk_mng.帧荷载信息 = (Object)changeProxyBitMapCnf_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0049:    //离线指示
                    nwk_mng.帧类型 = "离线指示";
                    nwk_mng.帧荷载信息 = (Object)leaveInd_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0051:    //心跳检测
                    nwk_mng.帧类型 = "心跳检测";
                    nwk_mng.帧荷载信息 = (Object)heartBeatCheck_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0055:    //发现列表
                    nwk_mng.帧类型 = "发现列表";
                    nwk_mng.帧荷载信息 = (Object)discoverNodeList_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x005D:    //延迟离线指示
                    nwk_mng.帧类型 = "延迟离线指示";
                    nwk_mng.帧荷载信息 = (Object)delayLeaveInd_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x005E:    //通信成功率上报
                    nwk_mng.帧类型 = "通信成功率上报";
                    nwk_mng.帧荷载信息 = (Object)successRateReport_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x005F:    //网络冲突上报
                    nwk_mng.帧类型 = "网络冲突上报";
                    nwk_mng.帧荷载信息 = (Object)nidRepeatReport_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0062:    //过零NTB采集指示
                    nwk_mng.帧类型 = "过零NTB采集";
                    nwk_mng.帧荷载信息 = (Object)zeroCrossNTBCollectInd_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0063:    //过零NTB上报
                    nwk_mng.帧类型 = "过零NTB上报";
                    nwk_mng.帧荷载信息 = (Object)zeroCrossNTBReport_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0064:    //网络诊断报文
                    nwk_mng.帧类型 = "网络诊断报文";
                    nwk_mng.帧荷载信息 = (Object)diagnose_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0070:    //无线信道冲突上报
                    nwk_mng.帧类型 = "无线信道冲突上报";
                    nwk_mng.帧荷载信息 = (Object)rfConflictRpt_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x00A0:    //采集数据汇聚上报
                    nwk_mng.帧类型 = "采集数据汇聚上报";
                    nwk_mng.帧荷载信息 = (Object)cltDataRpt_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                default:
                    nwk_mng.帧类型 = "NWK";
                    if (simple_flag != 1)
                    {
                        byte[] data = new byte[bpsz - start];
                        Array.Copy(buf, start, data, 0, data.Length);
                        nwk_mng.帧荷载信息 = data;
                    }
                    break;
            }

            if (nwk_mng.帧荷载信息 == null)
                nwk_mng.帧类型 = "NWK_err";

            return nwk_mng;
        }

        public static nwk_mng_c nwk_mng_deal_gw(byte[] buf, int bpsz, int start, UInt16 type, ref int simple_flag, ref string detail)
        {
            nwk_mng_c nwk_mng = new nwk_mng_c();
            switch (type)
            {
                case 0x0000:    //关联请求
                    nwk_mng.帧类型 = "关联请求";
                    nwk_mng.帧荷载信息 = (Object)assocReq_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0001:    //关联确认
                    nwk_mng.帧类型 = "关联确认";
                    nwk_mng.帧荷载信息 = (Object)assocCnf_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0003:    //代理变更请求
                    nwk_mng.帧类型 = "代理变更请求";
                    nwk_mng.帧荷载信息 = (Object)changeProxyReq_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0034:    //关联指示
                    nwk_mng.帧类型 = "关联指示";
                    nwk_mng.帧荷载信息 = (Object)assocInd_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0004:    //代理变更确认
                    nwk_mng.帧类型 = "代理变更确认";
                    nwk_mng.帧荷载信息 = (Object)changeProxyCnf_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0002:    //关联汇总指示
                    nwk_mng.帧类型 = "关联汇总指示";
                    nwk_mng.帧荷载信息 = (Object)assocGatherInd_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0005:    //代理变更确认位图版
                    nwk_mng.帧类型 = "代理变更确认位图版";
                    nwk_mng.帧荷载信息 = (Object)changeProxyBitMapCnf_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0006:    //离线指示
                    nwk_mng.帧类型 = "离线指示";
                    nwk_mng.帧荷载信息 = (Object)delayLeaveInd_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0007:    //心跳检测
                    nwk_mng.帧类型 = "心跳检测";
                    nwk_mng.帧荷载信息 = (Object)heartBeatCheck_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0008:    //发现列表
                    nwk_mng.帧类型 = "发现列表";
                    nwk_mng.帧荷载信息 = (Object)discoverNodeList_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x005D:    //延迟离线指示
                    nwk_mng.帧类型 = "延迟离线指示";
                    nwk_mng.帧荷载信息 = (Object)delayLeaveInd_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0009:    //通信成功率上报
                    nwk_mng.帧类型 = "通信成功率上报";
                    nwk_mng.帧荷载信息 = (Object)successRateReport_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x000A:    //网络冲突上报
                    nwk_mng.帧类型 = "网络冲突上报";
                    nwk_mng.帧荷载信息 = (Object)nidRepeatReport_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x000B:    //过零NTB采集指示
                    nwk_mng.帧类型 = "过零NTB采集";
                    nwk_mng.帧荷载信息 = (Object)zeroCrossNTBCollectInd_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x000C:    //过零NTB上报
                    nwk_mng.帧类型 = "过零NTB上报";
                    nwk_mng.帧荷载信息 = (Object)zeroCrossNTBReport_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x004F:    //网络诊断报文
                    nwk_mng.帧类型 = "网络诊断报文";
                    nwk_mng.帧荷载信息 = (Object)diagnose_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0050:    //路由请求
                    nwk_mng.帧类型 = "路由请求";
                    nwk_mng.帧荷载信息 = (Object)routeRequest_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0051:    //路由回复
                    nwk_mng.帧类型 = "路由回复";
                    nwk_mng.帧荷载信息 = (Object)routeReply_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0052:    //路由错误
                    nwk_mng.帧类型 = "路由错误";
                    nwk_mng.帧荷载信息 = (Object)routeError_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0053:    //路由应答
                    nwk_mng.帧类型 = "路由应答";
                    nwk_mng.帧荷载信息 = (Object)routeAck_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0054:    //链路确认请求
                    nwk_mng.帧类型 = "链路确认请求";
                    nwk_mng.帧荷载信息 = (Object)linkConfirmRequest_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x0055:    //链路确认请求
                    nwk_mng.帧类型 = "链路确认回应";
                    nwk_mng.帧荷载信息 = (Object)linkConfirmResponse_deal_gw(buf, bpsz, start, ref simple_flag, ref detail);
                    break;


                case 0x0070:    //无线信道冲突上报
                    nwk_mng.帧类型 = "无线信道冲突上报";
                    nwk_mng.帧荷载信息 = (Object)rfConflictRpt_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                case 0x00A0:    //采集数据汇聚上报
                    nwk_mng.帧类型 = "采集数据汇聚上报";
                    nwk_mng.帧荷载信息 = (Object)cltDataRpt_deal(buf, bpsz, start, ref simple_flag, ref detail);
                    break;

                default:
                    nwk_mng.帧类型 = "NWK";
                    if (simple_flag != 1)
                    {
                        byte[] data = new byte[bpsz - start];
                        Array.Copy(buf, start, data, 0, data.Length);
                        nwk_mng.帧荷载信息 = data;
                    }
                    break;
            }

            if (nwk_mng.帧荷载信息 == null)
                nwk_mng.帧类型 = "NWK_err";

            return nwk_mng;
        }

        public static MMAssocReq_c assocReq_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            UInt16 tmp16 = 0;
            MMAssocReq_c assocReq = new MMAssocReq_c();

            if (buf.Length < (start + 68))
            {
                return null;
            }

            if (simple_flag != 1)
            {
                Array.Copy(buf, start, assocReq.原始数据, 0, assocReq.原始数据.Length);
            }

            Array.Copy(buf, start, assocReq.站点MAC地址, 0, 6);
            for (int i = 0; i < 5; i++)
            {
                assocReq.PCO  += comFunc.ToUInt16(buf, start + 6 + i * 2).ToString("X4") + "  ";
            }

            for (int i = 0; i < 3; i++)
            {
                if (buf[start + 16 + i] == 1)
                {
                    assocReq.相线 += "A相|";
                }
                else if (buf[start + 16 + i] == 2)
                {
                    assocReq.相线 += "B相|";
                }
                else if (buf[start + 16 + i] == 3)
                {
                    assocReq.相线 += "C相|";
                }
                else
                {
                    assocReq.相线 += buf[start + 16 + i].ToString() + "|";
                }

            }

            if (buf[start + 19] == 0x01)
            {
                assocReq.设备类型 = "抄控器";
            }
            else if (buf[start + 19] == 0x02)
            {
                assocReq.设备类型 = "集中器通信模块";
            }
            else if (buf[start + 19] == 0x03)
            {
                assocReq.设备类型 = "单相电表通信模块";
            }
            else if (buf[start + 19] == 0x04)
            {
                assocReq.设备类型 = "中继器";
            }
            else if (buf[start + 19] == 0x05)
            {
                assocReq.设备类型 = "II 型采集器";
            }
            else if (buf[start + 19] == 0x06)
            {
                assocReq.设备类型 = "I 型采集器";
            }
            else if (buf[start + 19] == 0x07)
            {
                assocReq.设备类型 = "三相表通信模块";
            }
            else
            {
                assocReq.设备类型 = "其他:" + buf[start + 19];
            }

            assocReq.resv1 = buf[start + 20];
            assocReq.resv2 = buf[start + 21];
            assocReq.MAC地址类型 = buf[start + 22];
//#if NWSM
            byte tmp;
            tmp = comFunc.BitField8(buf[start + 23], 0, 2);
            if (tmp == 0)
            {
                assocReq.模块类型 = "HPLC";
            }
            else if (tmp == 1)
            {
                assocReq.模块类型 = "双模";
            }
            else if (tmp == 2)
            {
                assocReq.模块类型 = "RF";
            }
            for (int i = 0; i < 5; i++)
            {
                tmp = comFunc.BitField8(buf[start + 23], 2+i, 1);
                if (tmp == 0)
                {
                    assocReq.PCO链路类型 += "HPLC  ";
                }
                else
                {
                    assocReq.PCO链路类型 += "RF  ";
                }
            }
//#else
//            assocReq.resv3 = buf[start + 23];
//#endif
            assocReq.站点关联随机数 = comFunc.ToUInt32(buf, start + 24).ToString("X8");
            Array.Copy(buf, start + 28, assocReq.厂家附加数据, 0, 18);
            //版本信息
            if (buf[start + 46] == 0)
            {
                assocReq.系统启动原因 = "0正常启动";
            }
            else if (buf[start + 46] == 1)
            {
                assocReq.系统启动原因 = "1断电重启";
            }
            else if (buf[start + 46] == 2)
            {
                assocReq.系统启动原因 = "2看门狗复位";
            }
            else if (buf[start + 46] == 3)
            {
                assocReq.系统启动原因 = "3程序指针异常";
            }
            else
            {
                assocReq.系统启动原因 = buf[start + 46].ToString();
            }

            assocReq.BOOT版本号 = buf[start + 46 + 1].ToString("X2");
            assocReq.软件版本号 = comFunc.ToUInt16(buf, start + 46 + 2).ToString("X4");
            tmp16 = comFunc.ToUInt16(buf, start + 46 + 4);
            assocReq.年 = (byte)comFunc.BitField16(tmp16, 0, 7);
            assocReq.月 = (byte)comFunc.BitField16(tmp16, 7, 4);
            assocReq.日 = (byte)comFunc.BitField16(tmp16, 11, 5);
            Array.Copy(buf, start + 46 + 6, assocReq.厂商代码, 0, 2);
            Array.Copy(buf, start + 46 + 8, assocReq.芯片代码, 0, 2);

            if (simple_flag != 1)
            {
                assocReq.硬复位累积次数 = comFunc.ToUInt16(buf, start + 56);
                assocReq.软复位累积次数 = comFunc.ToUInt16(buf, start + 58);
                assocReq.代理类型 = buf[start + 60];
                assocReq.组网序列号 = buf[start + 61];
                assocReq.resv4 = comFunc.BitField8(buf[start + 62], 0, 1);
                assocReq.管理消息版本 = comFunc.BitField8(buf[start + 62], 1, 4);
                assocReq.resv5 = comFunc.BitField8(buf[start + 62], 5, 3);
                assocReq.支持的频段 = comFunc.BitField8(buf[start + 63], 0, 2) == 0 ? "仅支持频段 0 和频段 1" : "支持频段 0、频段 1、频段 2";
                assocReq.resv6 = comFunc.BitField8(buf[start + 63], 2, 6);
                assocReq.端到端序列号 = comFunc.ToUInt32(buf, start + 64);
            }
            else
            {
                detail += assocReq.相线;
                detail += "设备类型:" + assocReq.设备类型;
#if NWSM
                detail += "|设备类型:" + assocReq.模块类型;
#endif
                detail += "|系统启动原因:" + assocReq.系统启动原因;
                detail += "|软件版本:" + assocReq.软件版本号;
                detail += "|版本时间:" + assocReq.年.ToString() + "-" + assocReq.月.ToString() + "-" + assocReq.日.ToString();
                detail += "|厂商代码:" + (char)assocReq.厂商代码[1] + (char)assocReq.厂商代码[0];
                detail += "|芯片代码:" + (char)assocReq.芯片代码[1] + (char)assocReq.芯片代码[0];
                detail += "|厂家附加数据:" + comFunc.ByteArryToHexStr_2(assocReq.厂家附加数据);
                if (assocReq.厂商代码[1] == 'F' && assocReq.厂商代码[0] == 'C')
                {
                    detail += "|入网原因:";
                    if (assocReq.厂家附加数据[3] == 0)
                    {
                        detail += "2个路由周期内,收不到任何信标帧";
                    }
                    else if (assocReq.厂家附加数据[3] == 1)
                    {
                        detail += "连续4个路由周期内，与PCO的通信成功率为0";
                    }
                    else if (assocReq.厂家附加数据[3] == 2)
                    {
                        detail += "组网序列号变化";
                    }
                    else if (assocReq.厂家附加数据[3] == 3)
                    {
                        detail += "收到离线指示";
                    }
                    else if (assocReq.厂家附加数据[3] == 4)
                    {
                        detail += "一级STA,检测到CCO地址变化,且已经连续一个周期";
                    }
                    else if (assocReq.厂家附加数据[3] == 5)
                    {
                        detail += "STA发现自己PCO变成STA，且已经连续一个路由周期";
                    }
                    else if (assocReq.厂家附加数据[3] == 6)
                    {
                        detail += "本站点的层级超过最大层级(15)限制";
                    }
                    else if (assocReq.厂家附加数据[3] == 7)
                    {
                        detail += "连续12路由周期CCO没有分配本站点发信标";
                    }
                    else if (assocReq.厂家附加数据[3] == 8)
                    {
                        detail += "连续5信标周期没有听到本NID信标，但是听到本CCO地址不同NID信标";
                    }
                    else
                    {
                        detail += assocReq.厂家附加数据[3];
                    }
                }
            }
            return assocReq;
        }

        public static MMAssocReq_c_gw assocReq_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            UInt16 tmp16 = 0;
            byte tmp;
            MMAssocReq_c_gw assocReq = new MMAssocReq_c_gw();

            if (buf.Length < (start + 88))
            {
                return null;
            }

            if (simple_flag != 1)
            {
                Array.Copy(buf, start, assocReq.原始数据, 0, assocReq.原始数据.Length);
            }

            Array.Copy(buf, start, assocReq.站点MAC地址, 0, 6);
            for (int i = 0; i < 5; i++)
            {
                assocReq.PCO += comFunc.ToUInt16(buf, start + 6 + i * 2).ToString("X4") + "  ";
            }

            for (int i = 0; i < 5; i++)
            {
                tmp = comFunc.BitField8(buf[start + 7 + i * 2], 4, 1);
                if (tmp == 0)
                {
                    assocReq.链路类型 += "高速载波链路";
                }
                else
                {
                    assocReq.链路类型 += "无线链路";
                }
            }

            for (int i = 0; i < 3; i++)
            {
                if (comFunc.BitField8(buf[start + 16], i * 2, 2)==1)
                {
                    assocReq.相线 += "A相|";
                }
                else if (comFunc.BitField8(buf[start + 16], i * 2, 2) == 2)
                {
                    assocReq.相线 += "B相|";
                }
                else if (comFunc.BitField8(buf[start + 16], i * 2, 2) == 3)
                {
                    assocReq.相线 += "C相|";
                }
                else
                {
                    assocReq.相线 +=  "未知相|";
                }

            }

            assocReq.resv2 = comFunc.BitField8(buf[start + 16], 6, 2);

            if (buf[start + 17] == 0x01)
            {
                assocReq.设备类型 = "抄控器";
            }
            else if (buf[start + 17] == 0x02)
            {
                assocReq.设备类型 = "集中器本地通信模块";
            }
            else if (buf[start + 17] == 0x03)
            {
                assocReq.设备类型 = "电表通信模块";
            }
            else if (buf[start + 17] == 0x04)
            {
                assocReq.设备类型 = "中继器";
            }
            else if (buf[start + 17] == 0x05)
            {
                assocReq.设备类型 = "II 型采集器";
            }
            else if (buf[start + 17] == 0x06)
            {
                assocReq.设备类型 = "I 型采集器";
            }
            else if (buf[start + 17] == 0x07)
            {
                assocReq.设备类型 = "三相表通信模块";
            }
            else
            {
                assocReq.设备类型 = "其他:" + buf[start + 17];
            }

            assocReq.MAC地址类型 = buf[start + 18];
            
            //#if NWSM
            
            tmp = comFunc.BitField8(buf[start + 19], 0, 2);
            if (tmp == 0)
            {
                assocReq.模块类型 = "HPLC";
            }
            else if (tmp == 1)
            {
                assocReq.模块类型 = "双模";
            }
            else if (tmp == 2)
            {
                assocReq.模块类型 = "RF";
            }

            assocReq.resv3 = comFunc.BitField8(buf[start + 19], 2, 6);

            //#else
            //            assocReq.resv3 = buf[start + 23];
            //#endif
            assocReq.站点关联随机数 = comFunc.ToUInt32(buf, start + 20).ToString("X8");
            Array.Copy(buf, start + 24, assocReq.厂家附加数据, 0, 18);
            //版本信息
            if (buf[start + 42] == 0)
            {
                assocReq.系统启动原因 = "0正常启动";
            }
            else if (buf[start + 42] == 1)
            {
                assocReq.系统启动原因 = "1断电重启";
            }
            else if (buf[start + 42] == 2)
            {
                assocReq.系统启动原因 = "2看门狗复位";
            }
            else if (buf[start + 42] == 3)
            {
                assocReq.系统启动原因 = "3程序指针异常";
            }
            else
            {
                assocReq.系统启动原因 = buf[start + 42].ToString();
            }

            assocReq.BOOT版本号 = buf[start + 42 + 1].ToString("X2");
            assocReq.软件版本号 = comFunc.ToUInt16(buf, start + 42 + 2).ToString("X4");
            tmp16 = comFunc.ToUInt16(buf, start + 42 + 4);
            assocReq.年 = (byte)comFunc.BitField16(tmp16, 0, 7);
            assocReq.月 = (byte)comFunc.BitField16(tmp16, 7, 4);
            assocReq.日 = (byte)comFunc.BitField16(tmp16, 11, 5);
            Array.Copy(buf, start + 42 + 6, assocReq.厂商代码, 0, 2);
            Array.Copy(buf, start + 42 + 8, assocReq.芯片代码, 0, 2);

            if (simple_flag != 1)
            {
                assocReq.硬复位累积次数 = comFunc.ToUInt16(buf, start + 52);
                assocReq.软复位累积次数 = comFunc.ToUInt16(buf, start + 54);
                assocReq.代理类型 = buf[start + 56];
               // assocReq.组网序列号 = buf[start + 61];
                assocReq.resv4 = comFunc.ToUInt24(buf, start + 57);
               // assocReq.管理消息版本 = comFunc.BitField8(buf[start + 62], 1, 4);
               // assocReq.resv5 = comFunc.BitField8(buf[start + 62], 5, 3);
                //assocReq.支持的频段 = comFunc.BitField8(buf[start + 63], 0, 2) == 0 ? "仅支持频段 0 和频段 1" : "支持频段 0、频段 1、频段 2";
               // assocReq.resv6 = comFunc.BitField8(buf[start + 63], 2, 6);
                assocReq.端到端序列号 = comFunc.ToUInt32(buf, start + 60);
                Array.Copy(buf, start + 64, assocReq.管理ID信息, 0, 24);
            }
            else
            {
                detail += assocReq.相线;
                detail += "设备类型:" + assocReq.设备类型;
#if NWSM
                detail += "|设备类型:" + assocReq.模块类型;
#endif
                detail += "|系统启动原因:" + assocReq.系统启动原因;
                detail += "|软件版本:" + assocReq.软件版本号;
                detail += "|版本时间:" + assocReq.年.ToString() + "-" + assocReq.月.ToString() + "-" + assocReq.日.ToString();
                detail += "|厂商代码:" + (char)assocReq.厂商代码[1] + (char)assocReq.厂商代码[0];
                detail += "|芯片代码:" + (char)assocReq.芯片代码[1] + (char)assocReq.芯片代码[0];
                detail += "|厂家附加数据:" + comFunc.ByteArryToHexStr_2(assocReq.厂家附加数据);
                if (assocReq.厂商代码[1] == 'F' && assocReq.厂商代码[0] == 'C')
                {
                    detail += "|入网原因:";
                    if (assocReq.厂家附加数据[3] == 0)
                    {
                        detail += "2个路由周期内,收不到任何信标帧";
                    }
                    else if (assocReq.厂家附加数据[3] == 1)
                    {
                        detail += "连续4个路由周期内，与PCO的通信成功率为0";
                    }
                    else if (assocReq.厂家附加数据[3] == 2)
                    {
                        detail += "组网序列号变化";
                    }
                    else if (assocReq.厂家附加数据[3] == 3)
                    {
                        detail += "收到离线指示";
                    }
                    else if (assocReq.厂家附加数据[3] == 4)
                    {
                        detail += "一级STA,检测到CCO地址变化,且已经连续一个周期";
                    }
                    else if (assocReq.厂家附加数据[3] == 5)
                    {
                        detail += "STA发现自己PCO变成STA，且已经连续一个路由周期";
                    }
                    else if (assocReq.厂家附加数据[3] == 6)
                    {
                        detail += "本站点的层级超过最大层级(15)限制";
                    }
                    else if (assocReq.厂家附加数据[3] == 7)
                    {
                        detail += "连续12路由周期CCO没有分配本站点发信标";
                    }
                    else if (assocReq.厂家附加数据[3] == 8)
                    {
                        detail += "连续5信标周期没有听到本NID信标，但是听到本CCO地址不同NID信标";
                    }
                    else
                    {
                        detail += assocReq.厂家附加数据[3];
                    }
                }
            }
            return assocReq;
        }

        public static MMeAssocCnf_c assocCnf_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 44))
            {
                return null;
            }

            UInt16 tmp16;
            MMeAssocCnf_c assocCnf = new MMeAssocCnf_c();
            assocCnf.提示信息 = "解析正确";
            Array.Copy(buf, start, assocCnf.站点MAC地址, 0, 6);

            if (buf[start + 6] == 0x00)
            {
                assocCnf.关联结果 = "关联请求成功";
            }
            else if (buf[start + 6] == 0x01)
            {
                assocCnf.关联结果 = "站点不在白名单";
            }
            else if (buf[start + 6] == 0x02)
            {
                assocCnf.关联结果 = "保留";
            }
            else if (buf[start + 6] == 0x03)
            {
                assocCnf.关联结果 = "加入的站点个数超过上限";
            }
            else if (buf[start + 6] == 0x04)
            {
                assocCnf.关联结果 = "没有设置白名单列表";
            }
            else if (buf[start + 6] == 0x05)
            {
                assocCnf.关联结果 = "代理站点个数超过上限";
            }
            else if (buf[start + 6] == 0x06)
            {
                assocCnf.关联结果 = "子站点个数超过上限";
            }
            else if (buf[start + 6] == 0x07)
            {
                assocCnf.关联结果 = "保留";
            }
            else if (buf[start + 6] == 0x08)
            {
                assocCnf.关联结果 = "重复的MAC地址";
            }
            else if (buf[start + 6] == 0x09)
            {
                assocCnf.关联结果 = "超过拓扑层级";
            }
            else if (buf[start + 6] == 0x0A)
            {
                assocCnf.关联结果 = "再次入网成功";
            }
            else if (buf[start + 6] == 0x0B)
            {
                assocCnf.关联结果 = "新的站点试图以自己的子站点为代理来入网";
            }
            else if (buf[start + 6] == 0x0C)
            {
                assocCnf.关联结果 = "组网拓扑中存在环路";
            }
            else if (buf[start + 6] == 0x0D)
            {
                assocCnf.关联结果 = "CCO端未知原因出错";
            }
            else
            {
                assocCnf.关联结果 = buf[start + 6].ToString("X2");
            }
            assocCnf.层级 = buf[start + 7];
            assocCnf.TEI = comFunc.ToUInt16(buf, start + 8).ToString("X3");
            assocCnf.PCO = comFunc.ToUInt16(buf, start + 10).ToString("X3");

#if NWSM
            assocCnf.链路类型 = comFunc.BitField8(buf[start + 15], 0, 1) == 0 ? "HPLC" : "RF";
            assocCnf.载波频段 = comFunc.BitField8(buf[start + 15], 1, 2);
            assocCnf.resv1 = comFunc.BitField8(buf[start + 15], 3, 5);
#else
            assocCnf.resv1 = buf[start + 15];
#endif

            if (simple_flag != 1)
            {
                assocCnf.总分包数 = buf[start + 12];
                assocCnf.分包序号 = buf[start + 13];
                assocCnf.最后一个分包标识 = buf[start + 14];
                
                assocCnf.站点关联随机数 = comFunc.ToUInt32(buf, start + 16).ToString("X8");
                assocCnf.重新关联时间ms = comFunc.ToUInt32(buf, start + 20);
                assocCnf.端到端序列号 = comFunc.ToUInt32(buf, start + 24);
                assocCnf.路径序号 = comFunc.ToUInt32(buf, start + 28);
                assocCnf.组网序列号 = buf[start + 32];

                assocCnf.管理消息版本 = comFunc.BitField8(buf[start + 33], 0, 4);
                assocCnf.探测频段标识符 = comFunc.BitField8(buf[start + 33], 4, 1);
                assocCnf.resv2 = comFunc.BitField8(buf[start + 33], 5, 3);
                assocCnf.resv3 = comFunc.ToUInt16(buf, start + 34);

                //路由表信息
                assocCnf.直连站点数 = comFunc.ToUInt16(buf, start + 36);
                assocCnf.直连代理数 = comFunc.ToUInt16(buf, start + 38);
                assocCnf.路由表大小 = comFunc.ToUInt16(buf, start +40);
                assocCnf.resv4 = comFunc.ToUInt16(buf, start +42);
                if (buf.Length < (start + 44 + assocCnf.路由表大小*2))
                {
                    assocCnf.提示信息 = "路由表需要大小:" + assocCnf.路由表大小 * 2 + "，缓冲区剩余长度：" + (buf.Length - (start + 44));
                    return assocCnf;
                }
                assocCnf.路由表 = new byte[assocCnf.路由表大小 * 2];
                Array.Copy(buf, start + 44, assocCnf.路由表, 0, assocCnf.路由表.Length);

                if (assocCnf.直连站点数 != 0)
                {
                    if (assocCnf.路由表.Length < assocCnf.直连站点数*2)
                    {
                        assocCnf.提示信息 = "路由表直连站点数:" + assocCnf.直连站点数 + "，路由表长度：" + assocCnf.路由表.Length;
                        return assocCnf;
                    }
                    assocCnf.直连站点 = new List<string>();
                    for (int i = 0; i < assocCnf.直连站点数; i++)
                    {
                        string connect_sta = "";
                        tmp16 = comFunc.ToUInt16(assocCnf.路由表, i * 2);
#if NWSM
                        connect_sta = "TEI[" + tmp16.ToString("X3") + "]";
                        connect_sta += "  链路类型"+ (comFunc.BitField16(tmp16, 12, 1) == 0 ? "HPLC" : "RF");
                        connect_sta += "  保留" + (byte)comFunc.BitField16(tmp16, 13, 3);
#else
                        connect_sta = "TEI[" + tmp16.ToString("X4") + "]";
#endif
                        assocCnf.直连站点.Add(connect_sta);
                    }
                }

                int idx = assocCnf.直连站点数 * 2;
                if (assocCnf.直连代理数 != 0)
                {
                    assocCnf.直连代理 = new List<connect_pco_c>();
                    for (int i = 0; i < assocCnf.直连代理数; i++)
                    {
                        if (assocCnf.路由表.Length < (idx+4))
                        {
                            assocCnf.提示信息 = "路由表长度：" + assocCnf.路由表.Length + "，检索直连代理溢出";
                            return assocCnf;
                        }
                        connect_pco_c connect_pco = new connect_pco_c();
                        tmp16 = comFunc.ToUInt16(assocCnf.路由表, idx);
#if NWSM
                        connect_pco.TEI = tmp16.ToString("X3");
                        connect_pco.链路类型 = comFunc.BitField16(tmp16, 12, 1) == 0 ? "HPLC" : "RF";
                        connect_pco.resv = (byte)comFunc.BitField16(tmp16, 13, 3);
#else
                        connect_pco.TEI = tmp16.ToString("X4");
#endif
                        connect_pco.子站点数 = comFunc.ToUInt16(assocCnf.路由表, idx + 2);
                        idx += 4;
                        if (connect_pco.子站点数 != 0)
                        {
                            connect_pco.子站点 = new List<string>();
                            for (int j = 0; j < connect_pco.子站点数; j++)
                            {
                                if (assocCnf.路由表.Length < (idx + 2))
                                {
                                    assocCnf.提示信息 = "路由表长度：" + assocCnf.路由表.Length + "，检索直连代理的子站点溢出";
                                    return assocCnf;
                                }
                                string connect_sta = "";
                                tmp16 = comFunc.ToUInt16(assocCnf.路由表, idx);
#if NWSM
                                connect_sta = "TEI[" + tmp16.ToString("X3") + "]";
                                connect_sta += "  链路类型"+ (comFunc.BitField16(tmp16, 12, 1) == 0 ? "HPLC" : "RF");
                                connect_sta += "  保留" + (byte)comFunc.BitField16(tmp16, 13, 3);
#else
                                connect_sta = "TEI[" + tmp16.ToString("X4") + "]";
#endif
                                connect_pco.子站点.Add(connect_sta);
                                idx += 2;
                            }
                        }
                        assocCnf.直连代理.Add(connect_pco);
                    }
                }
                assocCnf.原始数据 = new byte[assocCnf.路由表.Length + 44];
                Array.Copy(buf, start, assocCnf.原始数据, 0, assocCnf.原始数据.Length);
            }
            else
            {
                detail += "站点地址:" + comFunc.ByteArryToHexStr_2(assocCnf.站点MAC地址);
                detail += "|结果:" + assocCnf.关联结果;
                detail += "|层级:" + assocCnf.层级;
                detail += "|TEI:" + assocCnf.TEI;
                detail += "|PCO:" + assocCnf.PCO;
#if NWSM
                detail += "|链路类型:" + assocCnf.链路类型;
                detail += "|载波频段:" + assocCnf.载波频段;
#endif
                tmp16 = comFunc.ToUInt16(buf, start + 40);
                if (buf.Length < (start + 44 + tmp16 * 2))
                {
                    detail += "|路由表长度错误";
                }
            }
            return assocCnf;
        }


        /*国网关联确认*/
        public static MMeAssocCnf_c_gw assocCnf_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 48))
            {
                return null;
            }

            UInt16 tmp16;
            MMeAssocCnf_c_gw assocCnf = new MMeAssocCnf_c_gw();
           // assocCnf.提示信息 = "解析正确";
            Array.Copy(buf, start, assocCnf.站点MAC地址, 0, 6);
            Array.Copy(buf, start + 6, assocCnf.CCO_MAC地址, 0, 6);

            if (buf[start + 12] == 0x00)
            {
                assocCnf.关联结果 = "关联请求成功";
            }
            else if (buf[start + 12] == 0x01)
            {
                assocCnf.关联结果 = "站点不在白名单";
            }
            else if (buf[start + 12] == 0x02)
            {
                assocCnf.关联结果 = "站点在黑名单中";
            }
            else if (buf[start + 12] == 0x03)
            {
                assocCnf.关联结果 = "加入的站点个数超过上限";
            }
            else if (buf[start + 12] == 0x04)
            {
                assocCnf.关联结果 = "没有设置白名单列表";
            }
            else if (buf[start + 12] == 0x05)
            {
                assocCnf.关联结果 = "代理站点个数超过上限";
            }
            else if (buf[start + 12] == 0x06)
            {
                assocCnf.关联结果 = "子站点个数超过上限";
            }
            else if (buf[start + 12] == 0x07)
            {
                assocCnf.关联结果 = "保留";
            }
            else if (buf[start + 12] == 0x08)
            {
                assocCnf.关联结果 = "重复的MAC地址";
            }
            else if (buf[start + 12] == 0x09)
            {
                assocCnf.关联结果 = "超过拓扑层级";
            }
            else if (buf[start + 12] == 0x0A)
            {
                assocCnf.关联结果 = "再次关联请求入网成功";
            }
            else if (buf[start + 12] == 0x0B)
            {
                assocCnf.关联结果 = "新的站点试图以自己的子站点为代理来入网";
            }
            else if (buf[start + 12] == 0x0C)
            {
                assocCnf.关联结果 = "组网拓扑中存在环路";
            }
            else if (buf[start + 12] == 0x0D)
            {
                assocCnf.关联结果 = "CCO端未知原因出错";
            }
            else if (buf[start + 12] == 0x0E)
            {
                assocCnf.关联结果 = "无线代理达上限";
            }
            else
            {
                assocCnf.关联结果 = buf[start + 12].ToString("X2");
            }
            assocCnf.层级 = buf[start + 13];
            assocCnf.站点TEI = comFunc.ToUInt16(buf, start + 14).ToString("X3");
            assocCnf.链路类型 = comFunc.BitField8(buf[start + 15], 4, 1) == 0 ? "HPLC" : "RF";
            if (comFunc.BitField8(buf[start + 15], 5, 2) == 0)
            {
                assocCnf.载波频段 = "1.953～11.96";
            }
            else if (comFunc.BitField8(buf[start + 15], 5, 2) == 1)
            {
                assocCnf.载波频段 = "2.441～5.615";
            }
            else if (comFunc.BitField8(buf[start + 15], 5, 2) == 2)
            {
                assocCnf.载波频段 = "0.781～2.930";
            }
            else if (comFunc.BitField8(buf[start + 15], 5, 2) == 3)
            {
                assocCnf.载波频段 = "1.758～2.930";
            }
    
            assocCnf.resv1 = comFunc.BitField8(buf[start + 15], 7, 1);
            assocCnf.代理TEI = comFunc.ToUInt16(buf, start + 16).ToString("X3");
            assocCnf.resv2 = comFunc.BitField8(buf[start + 17], 4, 4);

#if NWSM

#else
            assocCnf.resv1 = buf[start + 15];
#endif

            if (simple_flag != 1)
            {
                assocCnf.总分包数 = buf[start + 18];
                assocCnf.分包序号 = buf[start + 19];
               // assocCnf.最后一个分包标识 = buf[start + 14];

                assocCnf.站点关联随机数 = comFunc.ToUInt32(buf, start + 20).ToString("X8");
                assocCnf.重新关联时间ms = comFunc.ToUInt32(buf, start + 24);
                assocCnf.端到端序列号 = comFunc.ToUInt32(buf, start + 28);
                assocCnf.路径序号 = comFunc.ToUInt32(buf, start + 32);

                assocCnf.resv3 = comFunc.ToUInt32(buf, start + 36);
                //assocCnf.组网序列号 = buf[start + 32];

                // assocCnf.管理消息版本 = comFunc.BitField8(buf[start + 33], 0, 4);
                //assocCnf.探测频段标识符 = comFunc.BitField8(buf[start + 33], 4, 1);
                // assocCnf.resv2 = comFunc.BitField8(buf[start + 33], 5, 3);

                //路由表信息
                assocCnf.直连站点数 = comFunc.ToUInt16(buf, start + 40);
                assocCnf.直连代理数 = comFunc.ToUInt16(buf, start + 42);
                assocCnf.路由表大小 = comFunc.ToUInt16(buf, start + 44);
                assocCnf.resv4 = comFunc.ToUInt16(buf, start + 46);
                if (buf.Length < (start + 48 + assocCnf.路由表大小 * 2))
                {
                    assocCnf.提示信息 = "路由表需要大小:" + assocCnf.路由表大小 * 2 + "，缓冲区剩余长度：" + (buf.Length - (start + 44));
                    return assocCnf;
                }
                assocCnf.路由表 = new byte[assocCnf.路由表大小 * 2];
                Array.Copy(buf, start + 48, assocCnf.路由表, 0, assocCnf.路由表.Length);

                if (assocCnf.直连站点数 != 0)
                {
                    if (assocCnf.路由表.Length < assocCnf.直连站点数 * 2)
                    {
                        assocCnf.提示信息 = "路由表直连站点数:" + assocCnf.直连站点数 + "，路由表长度：" + assocCnf.路由表.Length;
                        return assocCnf;
                    }
                    assocCnf.直连站点 = new List<string>();
                    for (int i = 0; i < assocCnf.直连站点数; i++)
                    {
                        string connect_sta = "";
                        tmp16 = comFunc.ToUInt16(assocCnf.路由表, i * 2);
#if NWSM
                        connect_sta = "TEI[" + tmp16.ToString("X3") + "]";
                        connect_sta += "  链路类型" + (comFunc.BitField16(tmp16, 12, 1) == 0 ? "HPLC" : "RF");
                        connect_sta += "  保留" + (byte)comFunc.BitField16(tmp16, 13, 3);
#else
                        connect_sta = "TEI[" + tmp16.ToString("X4") + "]";
#endif
                        assocCnf.直连站点.Add(connect_sta);
                    }
                }

                int idx = assocCnf.直连站点数 * 2;
                if (assocCnf.直连代理数 != 0)
                {
                    assocCnf.直连代理 = new List<connect_pco_c>();
                    for (int i = 0; i < assocCnf.直连代理数; i++)
                    {
                        if (assocCnf.路由表.Length < (idx + 4))
                        {
                            assocCnf.提示信息 = "路由表长度：" + assocCnf.路由表.Length + "，检索直连代理溢出";
                            return assocCnf;
                        }
                        connect_pco_c connect_pco = new connect_pco_c();
                        tmp16 = comFunc.ToUInt16(assocCnf.路由表, idx);
#if NWSM
                        connect_pco.TEI = tmp16.ToString("X3");
                        connect_pco.链路类型 = comFunc.BitField16(tmp16, 12, 1) == 0 ? "HPLC" : "RF";
                        connect_pco.resv = (byte)comFunc.BitField16(tmp16, 13, 3);
#else
                        connect_pco.TEI = tmp16.ToString("X4");
#endif
                        connect_pco.子站点数 = comFunc.ToUInt16(assocCnf.路由表, idx + 2);
                        idx += 4;
                        if (connect_pco.子站点数 != 0)
                        {
                            connect_pco.子站点 = new List<string>();
                            for (int j = 0; j < connect_pco.子站点数; j++)
                            {
                                if (assocCnf.路由表.Length < (idx + 2))
                                {
                                    assocCnf.提示信息 = "路由表长度：" + assocCnf.路由表.Length + "，检索直连代理的子站点溢出";
                                    return assocCnf;
                                }
                                string connect_sta = "";
                                tmp16 = comFunc.ToUInt16(assocCnf.路由表, idx);
#if NWSM
                                connect_sta = "TEI[" + tmp16.ToString("X3") + "]";
                                connect_sta += "  链路类型" + (comFunc.BitField16(tmp16, 12, 1) == 0 ? "HPLC" : "RF");
                                connect_sta += "  保留" + (byte)comFunc.BitField16(tmp16, 13, 3);
#else
                                connect_sta = "TEI[" + tmp16.ToString("X4") + "]";
#endif
                                connect_pco.子站点.Add(connect_sta);
                                idx += 2;
                            }
                        }
                        assocCnf.直连代理.Add(connect_pco);
                    }
                }
                assocCnf.原始数据 = new byte[assocCnf.路由表.Length + 44];
                Array.Copy(buf, start, assocCnf.原始数据, 0, assocCnf.原始数据.Length);
            }
            else
            {
                detail += "站点地址:" + comFunc.ByteArryToHexStr_2(assocCnf.站点MAC地址);
                detail += "|结果:" + assocCnf.关联结果;
                detail += "|层级:" + assocCnf.层级;
                detail += "|TEI:" + assocCnf.站点TEI;
                detail += "|PCO:" + assocCnf.代理TEI;
#if NWSM
                detail += "|链路类型:" + assocCnf.链路类型;
                detail += "|载波频段:" + assocCnf.载波频段;
#endif
                tmp16 = comFunc.ToUInt16(buf, start + 40);
                if (buf.Length < (start + 44 + tmp16 * 2))
                {
                    detail += "|路由表长度错误";
                }
            }
            return assocCnf;
        }


        public static MMeChangeProxyReq_s changeProxyReq_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 40))
            {
                return null;
            }
            MMeChangeProxyReq_s changeProxyReq = new MMeChangeProxyReq_s();

            if (simple_flag != 1)
            {
                Array.Copy(buf, start, changeProxyReq.原始数据, 0, changeProxyReq.原始数据.Length);
            }

            changeProxyReq.站点TEI = comFunc.ToUInt16(buf, start + 0).ToString("X3");
            for (int i = 0; i < 5; i++)
            {
                changeProxyReq.新代理TEI[i] = comFunc.ToUInt16(buf, start + 2 + i * 2).ToString("X3");
            }
            changeProxyReq.旧代理TEI = comFunc.ToUInt16(buf, start + 12).ToString("X3");
            if (buf[start + 14] == 2)
            {
                changeProxyReq.代理类型 = "动态代理";
            }
            else
            {
                changeProxyReq.代理类型 = "保留:" + buf[start + 14].ToString();
            }
            if (buf[start + 15] == 1)
            {
                changeProxyReq.原因 = "周期代理变更";
            }
            else if(buf[start + 15] == 2)
            {
                changeProxyReq.原因 = "快速代理变更";
            }
            else
            {
                changeProxyReq.原因 = "保留:" + buf[start + 15].ToString();

            }
#if NWSM
            for (int i = 0; i < 5; i++)
            {
                changeProxyReq.新PCO链路类型[i] = comFunc.BitField8(buf[start + 19], i, 1) == 0 ? "HPLC" : "RF";
            }
            changeProxyReq.resv1 = comFunc.BitField8(buf[start + 19], 5, 3);
#else
            changeProxyReq.resv1 = buf[start + 19];
#endif

            if (simple_flag != 1)
            {
                for (int i = 0; i < 3; i++)
                {
                    if (buf[start + 16 + i] == 1)
                    {
                        changeProxyReq.相线[i] = "A";
                    }
                    else if (buf[start + 16 + i] == 2)
                    {
                        changeProxyReq.相线[i] = "B";
                    }
                    else if (buf[start + 16 + i] == 3)
                    {
                        changeProxyReq.相线[i] = "C";
                    }
                    else
                    {
                        changeProxyReq.相线[i] = "未知";
                    }
                }
                
                changeProxyReq.端到端序列号 = comFunc.ToUInt32(buf, start + 20);
                changeProxyReq.组网序列号 = buf[start + 24];

                Array.Copy(buf, start + 25, changeProxyReq.resv2, 0, 15);
            }
            else
            {
                detail += "TEI:" + changeProxyReq.站点TEI;
                detail += "|新代理TEI:";
                for (int i = 0; i < 5; i++)
                {
#if NWSM
                    detail += changeProxyReq.新代理TEI[i] + "-"+changeProxyReq.新PCO链路类型[i] + ",";
#else
                    detail += changeProxyReq.新代理TEI[i] + ",";
#endif
                }
                detail += "|旧代理TEI:" + changeProxyReq.旧代理TEI ;
                detail += "|原因:" + changeProxyReq.原因;
            }
            return changeProxyReq;
        }

        /*国网代理变更请求*/
        public static MMeChangeProxyReq_s_gw changeProxyReq_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 40))
            {
                return null;
            }
            MMeChangeProxyReq_s_gw changeProxyReq = new MMeChangeProxyReq_s_gw();

            if (simple_flag != 1)
            {
                Array.Copy(buf, start, changeProxyReq.原始数据, 0, changeProxyReq.原始数据.Length);
            }

            changeProxyReq.站点TEI = comFunc.ToUInt16(buf, start + 0).ToString("X3");
            changeProxyReq.resv1 = comFunc.BitField8(buf[1], 4, 4);
            for (int i = 0; i < 5; i++)
            {
                changeProxyReq.新代理TEI[i] = comFunc.ToUInt16(buf, start + 2 + i * 2).ToString("X3");
            }
            changeProxyReq.旧代理TEI = comFunc.ToUInt16(buf, start + 12).ToString("X3");
            if (buf[start + 14] == 0)
            {
                changeProxyReq.代理类型 = "动态代理";
            }
            else
            {
                changeProxyReq.代理类型 = "保留:" + buf[start + 14].ToString();
            }
            if (buf[start + 15] == 0)
            {
                changeProxyReq.原因 = "未知";
            }
            else if (buf[start + 15] == 1)
            {
                changeProxyReq.原因 = "周期代理变更";
            }
            else
            {
                changeProxyReq.原因 = "保留:" + buf[start + 15].ToString();

            }
#if NWSM
            for (int i = 0; i < 5; i++)
            {
                changeProxyReq.链路类型[i] = comFunc.BitField8(buf[start + 3 + i * 2], 4, 1) == 0 ? "高速载波链路" : "无线链路";
            }
            changeProxyReq.resv2 = comFunc.BitField8(buf[start + 13], 4, 4);
#else
            changeProxyReq.resv1 = buf[start + 19];
#endif

            if (simple_flag != 1)
            {
                
                    if (comFunc.BitField8(buf[start + 20], 0, 6) == 0)
                    {
                        changeProxyReq.相线 = "未知";
                    }
                    else if (comFunc.BitField8(buf[start + 20], 0, 6) == 1)
                    {
                        changeProxyReq.相线 = "A";
                    }
                    else if (comFunc.BitField8(buf[start + 20], 0, 6) == 2)
                    {
                        changeProxyReq.相线 = "B";
                    }
                    else if (comFunc.BitField8(buf[start + 20], 0, 6) == 3)
                    {
                        changeProxyReq.相线 = "C";
                    }
                    else
                    {
                        changeProxyReq.相线 = "无效";
                    }

                changeProxyReq.resv3 = comFunc.BitField8(buf[start + 20], 6, 2);
                changeProxyReq.端到端序列号 = comFunc.ToUInt32(buf, start + 16);
               // changeProxyReq.组网序列号 = buf[start + 24];

                Array.Copy(buf, start + 21, changeProxyReq.resv4, 0, 3);
            }
            else
            {
                detail += "TEI:" + changeProxyReq.站点TEI;
                detail += "|新代理TEI:";
                for (int i = 0; i < 5; i++)
                {
#if NWSM
                    detail += changeProxyReq.新代理TEI[i] + "-" + changeProxyReq.链路类型[i] + ",";
#else
                    detail += changeProxyReq.新代理TEI[i] + ",";
#endif
                }
                detail += "|旧代理TEI:" + changeProxyReq.旧代理TEI;
                detail += "|原因:" + changeProxyReq.原因;
            }
            return changeProxyReq;
        }

        public static MMeAssocInd_s assocInd_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 72))
            {
                return null;
            }

            UInt16 tmp16;
            MMeAssocInd_s assocInd = new MMeAssocInd_s();

            if (buf[start ] == 0x00)
            {
                assocInd.结果 = "关联请求成功";
            }
            else if (buf[start] == 0x01)
            {
                assocInd.结果 = "站点不在白名单";
            }
            else if (buf[start] == 0x02)
            {
                assocInd.结果 = "保留";
            }
            else if (buf[start] == 0x03)
            {
                assocInd.结果 = "加入的站点个数超过上限";
            }
            else if (buf[start] == 0x04)
            {
                assocInd.结果 = "没有设置白名单列表";
            }
            else if (buf[start] == 0x05)
            {
                assocInd.结果 = "代理站点个数超过上限";
            }
            else if (buf[start] == 0x06)
            {
                assocInd.结果 = "子站点个数超过上限";
            }
            else if (buf[start] == 0x07)
            {
                assocInd.结果 = "没有回复";
            }
            else if (buf[start] == 0x08)
            {
                assocInd.结果 = "重复的MAC地址";
            }
            else if (buf[start] == 0x09)
            {
                assocInd.结果 = "超过拓扑层级";
            }
            else if (buf[start] == 0x0A)
            {
                assocInd.结果 = "再次入网成功";
            }
            else if (buf[start] == 0x0B)
            {
                assocInd.结果 = "新的站点试图以自己的子站点为代理来入网";
            }
            else if (buf[start] == 0x0C)
            {
                assocInd.结果 = "组网拓扑中存在环路";
            }
            else if (buf[start] == 0x0D)
            {
                assocInd.结果 = "CCO端未知原因出错";
            }
            else
            {
                assocInd.结果 = buf[start].ToString("X2");
            }
            assocInd.站点层级 = buf[start + 1];
            Array.Copy(buf, start + 2, assocInd.站点MAC地址, 0, 6);
            Array.Copy(buf, start + 8, assocInd.CCO_MAC, 0, 6);
            assocInd.站点TEI = comFunc.ToUInt16(buf, start + 14).ToString("X3");
            assocInd.代理TEI = comFunc.ToUInt16(buf, start + 16).ToString("X3");

#if NWSM
            assocInd.链路类型 = comFunc.BitField8(buf[start + 18], 0, 1) == 0 ? "HPLC" : "RF";
            assocInd.载波频段 = comFunc.BitField8(buf[start + 18], 1, 2);
            assocInd.resv = comFunc.BitField8(buf[start + 18], 3, 5);
            assocInd.resv1[0] = buf[start + 19];
            assocInd.resv1[1] = buf[start + 20];
#else
            for (int i = 0; i < 3; i++)
            {
                assocInd.resv1[i] = buf[start + 18 + i];
            }
#endif

            if (simple_flag != 1)
            {
                
                assocInd.分包序号 = buf[start + 21];
                assocInd.总分包数 = buf[start + 22];
                assocInd.最后一个分包标识 = buf[start + 23];
                assocInd.站点关联随机数 = comFunc.ToUInt32(buf, start + 24).ToString("X8");
                Array.Copy(buf, start + 28, assocInd.resv2, 0, 17);
                assocInd.组网序列号 = buf[start + 45];
                for (int i = 0; i < 2; i++)
                {
                    assocInd.resv3[i] = buf[start + 46 + i];
                }
                assocInd.重新关联时间ms = comFunc.ToUInt32(buf, start + 48);
                assocInd.端到端序列号 = comFunc.ToUInt32(buf, start + 52);
                Array.Copy(buf, start + 56, assocInd.resv4, 0, 8);

                //路由表信息
                assocInd.直连站点数 = comFunc.ToUInt16(buf, start + 64);
                assocInd.直连代理数 = comFunc.ToUInt16(buf, start + 66);
                assocInd.路由表大小 = comFunc.ToUInt16(buf, start + 68);
                assocInd.resv5 = comFunc.ToUInt16(buf, start + 70);
                if (buf.Length < (start + 72 + assocInd.路由表大小 * 2))
                {
                    assocInd.提示信息 = "路由表需要大小:" + assocInd.路由表大小 * 2 + "，缓冲区剩余长度：" + (buf.Length - (start + 72));
                    return assocInd;
                }

                assocInd.路由表 = new byte[assocInd.路由表大小 * 2];
                Array.Copy(buf, start + 72, assocInd.路由表, 0, assocInd.路由表.Length);

                if (assocInd.直连站点数 != 0)
                {
                    if (assocInd.路由表.Length < assocInd.直连站点数 * 2)
                    {
                        assocInd.提示信息 = "路由表直连站点数:" + assocInd.直连站点数 + "，路由表长度：" + assocInd.路由表.Length;
                        return assocInd;
                    }
                    assocInd.直连站点 = new List<string>();
                    for (int i = 0; i < assocInd.直连站点数; i++)
                    {
                        
                        string connect_sta = "";
                        tmp16 = comFunc.ToUInt16(assocInd.路由表, i * 2);
#if NWSM
                        connect_sta = "TEI[" + tmp16.ToString("X3") + "]";
                        connect_sta += "  链路类型"+ (comFunc.BitField16(tmp16, 12, 1) == 0 ? "HPLC" : "RF");
                        connect_sta += "  保留" + (byte)comFunc.BitField16(tmp16, 13, 3);
#else
                        connect_sta = "TEI[" + tmp16.ToString("X4") + "]";
#endif
                        assocInd.直连站点.Add(connect_sta);
                    }
                }

                int idx = assocInd.直连站点数 * 2;
                if (assocInd.直连代理数 != 0)
                {
                    assocInd.直连代理 = new List<connect_pco_c>();
                    for (int i = 0; i < assocInd.直连代理数; i++)
                    {
                        if (assocInd.路由表.Length < (idx + 4))
                        {
                            assocInd.提示信息 = "路由表长度：" + assocInd.路由表.Length + "，检索直连代理溢出";
                            return assocInd;
                        }
                        connect_pco_c connect_pco = new connect_pco_c();
                        tmp16 = comFunc.ToUInt16(assocInd.路由表, idx);
#if NWSM
                        connect_pco.TEI = tmp16.ToString("X3");
                        connect_pco.链路类型 = comFunc.BitField16(tmp16, 12, 1) == 0 ? "HPLC" : "RF";
                        connect_pco.resv = (byte)comFunc.BitField16(tmp16, 13, 3);
#else
                        connect_pco.TEI = tmp16.ToString("X4");
#endif
                        connect_pco.子站点数 = comFunc.ToUInt16(assocInd.路由表, idx + 2);
                        idx += 4;
                        if (connect_pco.子站点数 != 0)
                        {
                            connect_pco.子站点 = new List<string>();
                            for (int j = 0; j < connect_pco.子站点数; j++)
                            {
                                if (assocInd.路由表.Length < (idx + 2))
                                {
                                    assocInd.提示信息 = "路由表长度：" + assocInd.路由表.Length + "，检索直连代理的子站点溢出";
                                    return assocInd;
                                }
                                string connect_sta = "";
                                tmp16 = comFunc.ToUInt16(assocInd.路由表, idx);
#if NWSM
                                connect_sta = "TEI[" + tmp16.ToString("X3") + "]";
                                connect_sta += "  链路类型"+ (comFunc.BitField16(tmp16, 12, 1) == 0 ? "HPLC" : "RF");
                                connect_sta += "  保留" + (byte)comFunc.BitField16(tmp16, 13, 3);
#else
                                connect_sta = "TEI[" + tmp16.ToString("X4") + "]";
#endif
                                idx += 2;
                            }
                        }
                        assocInd.直连代理.Add(connect_pco);
                    }
                }

                assocInd.原始数据 = new byte[72 + assocInd.路由表.Length];
                Array.Copy(buf, start, assocInd.原始数据, 0, assocInd.原始数据.Length);
            }
            else
            {
                detail += "站点地址:" + comFunc.ByteArryToHexStr_2(assocInd.站点MAC地址);
                detail += "|结果:" + assocInd.结果;
                detail += "|层级:" + assocInd.站点层级;
                detail += "|CCO地址:" + comFunc.ByteArryToHexStr_2(assocInd.CCO_MAC);
                detail += "|TEI:" + assocInd.站点TEI;
                detail += "|PCO:" + assocInd.代理TEI;
#if NWSM
                detail += "|链路类型:" + assocInd.链路类型;
                detail += "|载波频段:" + assocInd.载波频段;
#endif
                tmp16 = comFunc.ToUInt16(buf, start + 68);
                if (buf.Length < (start + 72 + tmp16 * 2))
                {
                    detail += "|路由表长度错误";
                }
            }

            return assocInd;
        }


        public static MMeAssocGatherInd_s assocGatherInd_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 28))
            {
                return null;
            }

            MMeAssocGatherInd_s assocGatherInd = new MMeAssocGatherInd_s();

            assocGatherInd.结果 = buf[start + 0];
            assocGatherInd.站点层级 = buf[start + 1];
            Array.Copy(buf, start + 2, assocGatherInd.CCO_MAC, 0, 6);
            assocGatherInd.代理TEI = comFunc.ToUInt16(buf, start + 8).ToString("X3");
            assocGatherInd.组网序列号 = buf[start + 10];
            assocGatherInd.汇总站点数 = buf[start + 11];
#if NWSM
            assocGatherInd.载波频段 = comFunc.BitField8(buf[start + 12], 0, 2);
            assocGatherInd.resv1 = comFunc.BitField8(buf[start + 12], 2, 6);
            Array.Copy(buf, start + 13, assocGatherInd.resv, 0, 15);
#else
            Array.Copy(buf, start + 12, assocGatherInd.resv, 0, 16);
#endif
            if (simple_flag != 1)
            {
                if (buf.Length < (start + 28 + assocGatherInd.汇总站点数 * 8))
                {
                    return assocGatherInd;
                }
                assocGatherInd.原始数据 = new byte[28 + assocGatherInd.汇总站点数 * 8];
                Array.Copy(buf, start, assocGatherInd.原始数据, 0, assocGatherInd.原始数据.Length);
                start += 28;
                if (assocGatherInd.汇总站点数 != 0)
                {
                    //站点信息
                    assocGatherInd.站点信息 = new byte[assocGatherInd.汇总站点数 * 8];
                    Array.Copy(buf, start, assocGatherInd.站点信息, 0, assocGatherInd.站点信息.Length);
                    assocGatherInd.站点信息字段 = new List<string>();
                    for (int i = 0; i < assocGatherInd.汇总站点数; i++)
                    {
                        byte[] mac = new byte[6];
                        string sta_info = "";
                        Array.Copy(assocGatherInd.站点信息, i * 8, mac, 0, 6);
                        sta_info = comFunc.ByteArryToHexStrWithoutBlock(mac);
                        sta_info += "  TEI[" + comFunc.ToUInt16(assocGatherInd.站点信息, i * 8 + 6).ToString("X4") + "]";
                        assocGatherInd.站点信息字段.Add(sta_info);
                    }
                }
            }
            else
            {
                detail += "结果:" + assocGatherInd.结果;
                detail += "|层级:" + assocGatherInd.站点层级.ToString();
                detail += "|CCO地址:" + comFunc.ByteArryToHexStr_2(assocGatherInd.CCO_MAC);
                detail += "|PCO:" + assocGatherInd.代理TEI;
                detail += "|站点数:" + assocGatherInd.汇总站点数.ToString();
#if NWSM
                detail += "|载波频段:" + assocGatherInd.载波频段;
#endif
                if (buf.Length < (start + 28 + assocGatherInd.汇总站点数 * 8))
                {
                    detail += "|站点信息长度有误";
                }
            }
            return assocGatherInd;
        }

        /*国网关联汇总指示*/
        public static MMeAssocGatherInd_s_gw assocGatherInd_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 16))
            {
                return null;
            }

            MMeAssocGatherInd_s_gw assocGatherInd = new MMeAssocGatherInd_s_gw();

            assocGatherInd.结果 = buf[start + 0];
            assocGatherInd.站点层级 = buf[start + 1];
            Array.Copy(buf, start + 2, assocGatherInd.CCO_MAC, 0, 6);
            assocGatherInd.代理TEI = comFunc.ToUInt16(buf, start + 8).ToString("X3");
            //assocGatherInd.组网序列号 = buf[start + 10];
            assocGatherInd.汇总站点数 = buf[start + 11];
#if NWSM
            assocGatherInd.载波频段 = comFunc.BitField8(buf[start + 9], 4, 2);
            assocGatherInd.resv1 = comFunc.BitField8(buf[start + 9], 6, 2);
            Array.Copy(buf, start + 12, assocGatherInd.resv3, 0, 4);
#else
            Array.Copy(buf, start + 12, assocGatherInd.resv, 0, 16);
#endif
            if (simple_flag != 1)
            {
                if (buf.Length < (start + 16 + assocGatherInd.汇总站点数 * 8))
                {
                    return assocGatherInd;
                }
                assocGatherInd.原始数据 = new byte[16 + assocGatherInd.汇总站点数 * 8];
                Array.Copy(buf, start, assocGatherInd.原始数据, 0, assocGatherInd.原始数据.Length);
                start += 16;
                if (assocGatherInd.汇总站点数 != 0)
                {
                    //站点信息
                    assocGatherInd.站点信息 = new byte[assocGatherInd.汇总站点数 * 8];
                    Array.Copy(buf, start, assocGatherInd.站点信息, 0, assocGatherInd.站点信息.Length);
                    assocGatherInd.站点信息字段 = new List<string>();
                    for (int i = 0; i < assocGatherInd.汇总站点数; i++)
                    {
                        byte[] mac = new byte[6];
                        string sta_info = "";
                        Array.Copy(assocGatherInd.站点信息, i * 8, mac, 0, 6);
                        sta_info = comFunc.ByteArryToHexStrWithoutBlock(mac);
                        sta_info += "  TEI[" + comFunc.ToUInt16(assocGatherInd.站点信息, i * 8 + 6).ToString("X4") + "]";
                        assocGatherInd.站点信息字段.Add(sta_info);
                    }
                }
            }
            else
            {
                detail += "结果:" + assocGatherInd.结果;
                detail += "|层级:" + assocGatherInd.站点层级.ToString();
                detail += "|CCO地址:" + comFunc.ByteArryToHexStr_2(assocGatherInd.CCO_MAC);
                detail += "|PCO:" + assocGatherInd.代理TEI;
                detail += "|站点数:" + assocGatherInd.汇总站点数.ToString();
#if NWSM
                detail += "|载波频段:" + assocGatherInd.载波频段;
#endif
                if (buf.Length < (start + 28 + assocGatherInd.汇总站点数 * 8))
                {
                    detail += "|站点信息长度有误";
                }
            }
            return assocGatherInd;
        }


        public static MMeChangeProxyCnf_s changeProxyCnf_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 32))
            {
                return null;
            }
            UInt32 tmp32 = 0;
            MMeChangeProxyCnf_s changeProxyCnf = new MMeChangeProxyCnf_s();
            tmp32 = comFunc.ToUInt32(buf, start);
            changeProxyCnf.结果 = tmp32 == 0 ? "变更成功" : ("保留:" + tmp32.ToString());
            changeProxyCnf.总分包数 = buf[start + 4];
            changeProxyCnf.分包序号 = buf[start + 5];
            changeProxyCnf.站点TEI = comFunc.ToUInt16(buf, start + 6).ToString("X3");
            changeProxyCnf.代理TEI = comFunc.ToUInt16(buf, start + 8).ToString("X3");
            changeProxyCnf.子站点数 = comFunc.ToUInt16(buf, start + 10);

#if NWSM
            changeProxyCnf.链路类型 = comFunc.BitField8(buf[start + 14], 0, 1) == 0 ? "HPLC" : "RF";
            changeProxyCnf.resv2[0] = comFunc.BitField8(buf[start + 14], 1, 7);
            changeProxyCnf.resv2[1] = buf[start + 15];
#else
            Array.Copy(buf, start + 14, changeProxyCnf.resv2, 0, 2);
#endif

            if (simple_flag != 1)
            {
                if (buf.Length < (start + 32 + changeProxyCnf.子站点数 * 2))
                {
                    return changeProxyCnf;
                }
                changeProxyCnf.原始数据 = new byte[32 + changeProxyCnf.子站点数*2];
                Array.Copy(buf, start, changeProxyCnf.原始数据, 0, changeProxyCnf.原始数据.Length);

                changeProxyCnf.resv1 = buf[start + 12];
                changeProxyCnf.组网序列号 = buf[start + 13];
                changeProxyCnf.端到端序列号 = comFunc.ToUInt32(buf, start + 16);
                changeProxyCnf.路径序号 = comFunc.ToUInt32(buf, start + 20);
                Array.Copy(buf, start + 24, changeProxyCnf.resv3, 0, 8);
                start += 32;

                if (changeProxyCnf.子站点数 != 0)
                {
                    //站点信息
                    changeProxyCnf.子站点条目 = new byte[changeProxyCnf.子站点数 * 2];
                    Array.Copy(buf, start, changeProxyCnf.子站点条目, 0, changeProxyCnf.子站点条目.Length);
                    changeProxyCnf.子站点条目字段 = new List<substa_info_c>();
                    for (int i = 0; i < changeProxyCnf.子站点数; i++)
                    {
                        substa_info_c substa_info = new substa_info_c();
                        substa_info.TEI = comFunc.ToUInt16(changeProxyCnf.子站点条目, i * 2);
                        changeProxyCnf.子站点条目字段.Add(substa_info);
                    }
                }
            }
            else
            {
                detail += "结果:" + changeProxyCnf.结果;
                detail += "|TEI:" + changeProxyCnf.站点TEI;
                detail += "|PCO:" + changeProxyCnf.代理TEI;
#if NWSM
                detail += "|链路类型:" + changeProxyCnf.链路类型;
#endif
                detail += "|子站点数:" + changeProxyCnf.子站点数;
                if (buf.Length < (start + 32 + changeProxyCnf.子站点数 * 2))
                {
                    detail += "|子站点条目数有误";
                }
            }
            return changeProxyCnf;
        }

        /*国网代理请求确认*/
        public static MMeChangeProxyCnf_s_gw changeProxyCnf_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 20))
            {
                return null;
            }          
            MMeChangeProxyCnf_s_gw changeProxyCnf = new MMeChangeProxyCnf_s_gw();
            
            changeProxyCnf.结果 = buf[start] == 0 ? "变更成功" : ("保留:" + buf[start].ToString());
            changeProxyCnf.总分包数 = buf[start + 1];
            changeProxyCnf.分包序号 = buf[start + 2];
            changeProxyCnf.站点TEI = comFunc.ToUInt16(buf, start + 4).ToString("X3");
            changeProxyCnf.链路类型 = comFunc.BitField8(buf[start + 5], 4, 1) == 0 ? "高速载波链路" : "无线链路";
            changeProxyCnf.resv1 = comFunc.BitField8(buf[start + 5], 5, 3);
            changeProxyCnf.代理TEI = comFunc.ToUInt16(buf, start + 6).ToString("X3");
            changeProxyCnf.resv2 = comFunc.BitField8(buf[start + 7], 4, 4);
            changeProxyCnf.端到端序列号 = comFunc.ToUInt32(buf, start + 8);
            changeProxyCnf.路径序号 = comFunc.ToUInt32(buf, start + 12);
            changeProxyCnf.子站点数 = comFunc.ToUInt16(buf, start + 16);
            Array.Copy(buf, start + 18, changeProxyCnf.resv4, 0, 2);
#if NWSM


#else
            
#endif
            if (simple_flag != 1)
            {
                if (buf.Length < (start + 20 + changeProxyCnf.子站点数 * 2))
                {
                    return changeProxyCnf;
                }
                changeProxyCnf.原始数据 = new byte[20 + changeProxyCnf.子站点数 * 2];
                Array.Copy(buf, start, changeProxyCnf.原始数据, 0, changeProxyCnf.原始数据.Length);

               // changeProxyCnf.resv1 = buf[start + 12];
                
                start += 20;

                if (changeProxyCnf.子站点数 != 0)
                {
                    //站点信息
                    changeProxyCnf.子站点条目 = new byte[changeProxyCnf.子站点数 * 2];
                    Array.Copy(buf, start, changeProxyCnf.子站点条目, 0, changeProxyCnf.子站点条目.Length);
                    changeProxyCnf.子站点条目字段 = new List<substa_info_c>();
                    for (int i = 0; i < changeProxyCnf.子站点数; i++)
                    {
                        substa_info_c substa_info = new substa_info_c();
                        substa_info.TEI = comFunc.ToUInt16(changeProxyCnf.子站点条目, i * 2);
                        changeProxyCnf.子站点条目字段.Add(substa_info);
                    }
                }
            }
            else
            {
                detail += "结果:" + changeProxyCnf.结果;
                detail += "|TEI:" + changeProxyCnf.站点TEI;
                detail += "|PCO:" + changeProxyCnf.代理TEI;
#if NWSM
                detail += "|链路类型:" + changeProxyCnf.链路类型;
#endif
                detail += "|子站点数:" + changeProxyCnf.子站点数;
                if (buf.Length < (start + 20 + changeProxyCnf.子站点数 * 2))
                {
                    detail += "|子站点条目数有误";
                }
            }
            return changeProxyCnf;
        }

        public static MMeChangeProxyBitMapCnf_s changeProxyBitMapCnf_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 148))
            {
                return null;
            }
            UInt32 tmp32 = 0;
            MMeChangeProxyBitMapCnf_s changeProxyBitMapCnf = new MMeChangeProxyBitMapCnf_s();

            if (simple_flag != 1)
            {
                Array.Copy(buf, start, changeProxyBitMapCnf.原始数据, 0, changeProxyBitMapCnf.原始数据.Length);
            }
            tmp32 = comFunc.ToUInt32(buf, start);
            changeProxyBitMapCnf.结果 = tmp32 == 0 ? "变更成功" : ("保留:" + tmp32.ToString());
            changeProxyBitMapCnf.站点TEI = comFunc.ToUInt16(buf, start + 4).ToString("X3");
            changeProxyBitMapCnf.代理TEI = comFunc.ToUInt16(buf, start + 6).ToString("X3");
#if NWSM
            changeProxyBitMapCnf.链路类型 = comFunc.BitField8(buf[start + 139], 0, 1) == 0 ? "HPLC" : "RF";
            changeProxyBitMapCnf.resv = comFunc.BitField8(buf[start + 139], 1, 7);
#else
            changeProxyBitMapCnf.resv = buf[start + 139];
#endif
            if (simple_flag != 1)
            {
                changeProxyBitMapCnf.组网序列号 = buf[start + 8];
                Array.Copy(buf, start + 9, changeProxyBitMapCnf.子站点位图, 0, 130);
                changeProxyBitMapCnf.端到端序列号 = comFunc.ToUInt32(buf, start + 140);
                changeProxyBitMapCnf.路径序号 = comFunc.ToUInt32(buf, start + 144);
            }
            else
            {
                detail += "结果:" + changeProxyBitMapCnf.结果;
                detail += "|TEI:" + changeProxyBitMapCnf.站点TEI;
                detail += "|PCO:" + changeProxyBitMapCnf.代理TEI;
#if NWSM
                detail += "|链路类型:" + changeProxyBitMapCnf.链路类型;
#endif

            }
            return changeProxyBitMapCnf;
        }


        /*国网代理变更确认（位图）*/
        public static MMeChangeProxyBitMapCnf_s_gw changeProxyBitMapCnf_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 20))
            {
                return null;
            }
           // UInt32 tmp32 = 0;
            MMeChangeProxyBitMapCnf_s_gw changeProxyBitMapCnf = new MMeChangeProxyBitMapCnf_s_gw();

            if (simple_flag != 1)
            {
                Array.Copy(buf, start, changeProxyBitMapCnf.原始数据, 0, changeProxyBitMapCnf.原始数据.Length);
            }
           // tmp32 = comFunc.ToUInt32(buf, start);
            changeProxyBitMapCnf.结果 = buf[start] == 0 ? "变更成功" : ("保留:" + buf[start].ToString());
            changeProxyBitMapCnf.resv1 = comFunc.BitField8(buf[start + 1], 0, 8);
            changeProxyBitMapCnf.位图大小 = comFunc.ToUInt16(buf, start+2);
            changeProxyBitMapCnf.站点TEI = comFunc.ToUInt16(buf, start + 4).ToString("X3");
            changeProxyBitMapCnf.链路类型 = comFunc.BitField8(buf[start + 5], 4, 1) == 0 ? "HPLC" : "RF";
            changeProxyBitMapCnf.resv2 = comFunc.BitField8(buf[start + 5], 5, 3);
            changeProxyBitMapCnf.代理TEI = comFunc.ToUInt16(buf, start + 6).ToString("X3");
            changeProxyBitMapCnf.resv3 = comFunc.BitField8(buf[start + 7], 4, 4);
             

            if (simple_flag != 1)
            {
                changeProxyBitMapCnf.端到端序列号 = comFunc.ToUInt32(buf, start + 8);
                changeProxyBitMapCnf.路径序号 = comFunc.ToUInt32(buf, start + 12);
                changeProxyBitMapCnf.resv4 = comFunc.ToUInt32(buf, start + 16);
                //changeProxyBitMapCnf.组网序列号 = buf[start + 8];
                Array.Copy(buf, start + 19, changeProxyBitMapCnf.子站点位图, 0, changeProxyBitMapCnf.位图大小);
                
            }
            else
            {
                detail += "结果:" + changeProxyBitMapCnf.结果;
                detail += "|TEI:" + changeProxyBitMapCnf.站点TEI;
                detail += "|PCO:" + changeProxyBitMapCnf.代理TEI;
#if NWSM
                detail += "|链路类型:" + changeProxyBitMapCnf.链路类型;
#endif

            }
            return changeProxyBitMapCnf;
        }

        public static MMeLeaveInd_s leaveInd_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 20))
            {
                return null;
            }
            UInt16 tmp16 = 0;
            MMeLeaveInd_s leaveInd = new MMeLeaveInd_s();

            if (simple_flag != 1)
            {
                Array.Copy(buf, start, leaveInd.原始数据, 0, leaveInd.原始数据.Length);
            }

            leaveInd.站点TEI = comFunc.ToUInt16(buf, start).ToString("X3");
            tmp16 = comFunc.ToUInt16(buf, start + 2);
            if (tmp16 == 0)
            {
                leaveInd.离线原因 = "STA未入网,收到其报文";
            }
            else if(tmp16 == 2)
            {
                leaveInd.离线原因 = "拓扑层级超过上限";
            }
            else if(tmp16 == 4)
            {
                leaveInd.离线原因 = "立即离线";
            }
            else
            {
                leaveInd.离线原因 = tmp16.ToString();
            }
            Array.Copy(buf, start + 4, leaveInd.站点MAC地址, 0, 6);
            leaveInd.代理TEI = comFunc.ToUInt16(buf, start + 10).ToString("X3");
            if (simple_flag != 1)
            {
                Array.Copy(buf, start + 12, leaveInd.resv, 0, 8);
            }
            else
            {
                detail += "TEI:" + leaveInd.站点TEI;
                detail += "|代理TEI:" + leaveInd.代理TEI;
                detail += "|离线原因:" + leaveInd.离线原因;
            }
            return leaveInd;
        }

        public static MMeDelayLeaveInd_s delayLeaveInd_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 16))
            {
                return null;
            }
            UInt16 tmp16 = 0;
            MMeDelayLeaveInd_s delayLeaveInd = new MMeDelayLeaveInd_s();

            tmp16 = comFunc.ToUInt16(buf, start + 0);
            if (tmp16 == 3)
            {
                delayLeaveInd.离线原因 = "不在最新的白名单中";
            }
            else
            {
                delayLeaveInd.离线原因 = tmp16.ToString();
            }

            delayLeaveInd.站点总数 = comFunc.ToUInt16(buf, start + 2);
            delayLeaveInd.延迟时间s = comFunc.ToUInt16(buf, start + 4);
            if (simple_flag != 1)
            {
                if (buf.Length < (start + 16 + delayLeaveInd.站点总数 * 6))
                {
                    return delayLeaveInd;
                }
                delayLeaveInd.原始数据 = new byte[16 + delayLeaveInd.站点总数 * 6];
                Array.Copy(buf, start, delayLeaveInd.原始数据, 0, delayLeaveInd.原始数据.Length);

                Array.Copy(buf, start + 6, delayLeaveInd.resv, 0, 10);
                if (delayLeaveInd.站点总数 != 0)
                {
                    delayLeaveInd.站点MAC地址 = new List<sta_mac_addr_c>();
                    for (int i = 0; i < delayLeaveInd.站点总数; i++)
                    {
                        sta_mac_addr_c sta_mac_addr = new sta_mac_addr_c();
                        Array.Copy(buf, start + 16 + i * 6, sta_mac_addr.MAC, 0, 6);
                        delayLeaveInd.站点MAC地址.Add(sta_mac_addr);
                    }
                }
            }
            else
            {
                detail += "站点总数:" + delayLeaveInd.站点总数;
                detail += "|延迟时间s:" + delayLeaveInd.延迟时间s;
                detail += "|离线原因:" + delayLeaveInd.离线原因;
                if (buf.Length < (start + 16 + delayLeaveInd.站点总数 * 6))
                {
                    detail += "|站点总数有误";
                }
            }
            return delayLeaveInd;
        }

        /*国网离线指示*/
        public static MMeDelayLeaveInd_s_gw delayLeaveInd_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 16))
            {
                return null;
            }
            UInt16 tmp16 = 0;
            MMeDelayLeaveInd_s_gw delayLeaveInd = new MMeDelayLeaveInd_s_gw();

            tmp16 = comFunc.ToUInt16(buf, start + 0);
            if (tmp16 == 0)
            {
                delayLeaveInd.离线原因 = "CCO通知站点立即离线";
            }
            else if(tmp16 == 1)
            {
                delayLeaveInd.离线原因 = "CCO判断网络拓扑的层级超过上限";
            }
            else if (tmp16 == 2)
            {
                delayLeaveInd.离线原因 = "CCO判断站点不在最新的白名单中";
            }
            else
            {
                delayLeaveInd.离线原因 = tmp16.ToString();
            }

            delayLeaveInd.站点总数 = comFunc.ToUInt16(buf, start + 2);
            delayLeaveInd.延迟时间s = comFunc.ToUInt16(buf, start + 4);
            if (simple_flag != 1)
            {
                if (buf.Length < (start + 16 + delayLeaveInd.站点总数 * 6))
                {
                    return delayLeaveInd;
                }
                delayLeaveInd.原始数据 = new byte[16 + delayLeaveInd.站点总数 * 6];
                Array.Copy(buf, start, delayLeaveInd.原始数据, 0, delayLeaveInd.原始数据.Length);

                Array.Copy(buf, start + 6, delayLeaveInd.resv, 0, 10);
                if (delayLeaveInd.站点总数 != 0)
                {
                    delayLeaveInd.站点MAC地址 = new List<sta_mac_addr_c>();
                    for (int i = 0; i < delayLeaveInd.站点总数; i++)
                    {
                        sta_mac_addr_c sta_mac_addr = new sta_mac_addr_c();
                        Array.Copy(buf, start + 16 + i * 6, sta_mac_addr.MAC, 0, 6);
                        delayLeaveInd.站点MAC地址.Add(sta_mac_addr);
                    }
                }
            }
            else
            {
                detail += "站点总数:" + delayLeaveInd.站点总数;
                detail += "|延迟时间s:" + delayLeaveInd.延迟时间s;
                detail += "|离线原因:" + delayLeaveInd.离线原因;
                if (buf.Length < (start + 16 + delayLeaveInd.站点总数 * 6))
                {
                    detail += "|站点总数有误";
                }
            }
            return delayLeaveInd;
        }


        public static MMeHeartBeatCheck_s heartBeatCheck_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            int num = 0;
            if (buf.Length < (start + 139))
            {
                return null;
            }
            MMeHeartBeatCheck_s heartBeatCheck = new MMeHeartBeatCheck_s();

            if (simple_flag != 1)
            {
                Array.Copy(buf, start, heartBeatCheck.原始数据, 0, heartBeatCheck.原始数据.Length);
            }

            heartBeatCheck.原始源TEI = comFunc.ToUInt16(buf, start).ToString("X3");
            heartBeatCheck.发现站点数最大的站点TEI = comFunc.ToUInt16(buf, start + 2).ToString("X3");
            heartBeatCheck.最大的发现站点数 = comFunc.ToUInt32(buf, start + 4);
            if (simple_flag != 1)
            {
                Array.Copy(buf, start + 8, heartBeatCheck.可发现站点TEI, 0, 130);
                heartBeatCheck.详细TEI = new List<string>();
                string tei_str = "";
                for (int i = 0; i < 130; i++)
                {
                    if (heartBeatCheck.可发现站点TEI[i] != 0)
                    {
                        for (int j = 0; j < 8; j++)
                        {
                            byte flag = (byte)((heartBeatCheck.可发现站点TEI[i] >> j) & 0x01);
                            if (flag == 1)
                            {
                                int now = i * 8 + j;
                                tei_str += now.ToString("X3") + "  ";
                                num++;
                                if(num >= 10)
                                {
                                    num = 0;
                                    heartBeatCheck.详细TEI.Add(tei_str);
                                    tei_str = "";
                                }
                            }
                        }
                    }
                }
                if (num != 0)
                {
                    num = 0;
                    heartBeatCheck.详细TEI.Add(tei_str);
                    tei_str = "";
                }
                heartBeatCheck.resv = buf[start + 138];
            }
            else
            {
                detail += "原始源TEI:" + heartBeatCheck.原始源TEI;
                detail += "|发现站点数最大的站点TEI:" + heartBeatCheck.发现站点数最大的站点TEI;
                detail += "|最大的发现站点数:" + heartBeatCheck.最大的发现站点数;
            }
            return heartBeatCheck;
        }

        /*国网心跳检测*/
        public static MMeHeartBeatCheck_s_gw heartBeatCheck_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            int num = 0;
            MMeHeartBeatCheck_s_gw heartBeatCheck = new MMeHeartBeatCheck_s_gw();
            if (buf.Length < (start + 8 + heartBeatCheck.位图大小))
            {
                return null;
            }

            if (simple_flag != 1)
            {
                heartBeatCheck.原始数据 = new byte[8 + heartBeatCheck.位图大小 * 2];
                Array.Copy(buf, start, heartBeatCheck.原始数据, 0, heartBeatCheck.原始数据.Length);
            }

            heartBeatCheck.原始源TEI = comFunc.ToUInt16(buf, start).ToString("X3");
            heartBeatCheck.resv1 = comFunc.BitField8(buf[start + 1], 4, 4);
            heartBeatCheck.发现站点数最大的站点TEI = comFunc.ToUInt16(buf, start + 2).ToString("X3");
            heartBeatCheck.resv2 = comFunc.BitField8(buf[start + 3], 4, 4);
            heartBeatCheck.最大的发现站点数 = comFunc.ToUInt16(buf, start + 4);
            heartBeatCheck.位图大小 = comFunc.ToUInt16(buf, start + 6);
            if (simple_flag != 1)
            {
                heartBeatCheck.发现站点位图 = new byte[ heartBeatCheck.位图大小 * 2];
                Array.Copy(buf, start + 8, heartBeatCheck.发现站点位图, 0, heartBeatCheck.位图大小);
                heartBeatCheck.详细TEI = new List<string>();
                string tei_str = "";
                for (int i = 0; i < heartBeatCheck.位图大小; i++)
                {
                    if (heartBeatCheck.发现站点位图[i] != 0)
                    {
                        for (int j = 0; j < 8; j++)
                        {
                            byte flag = (byte)((heartBeatCheck.发现站点位图[i] >> j) & 0x01);
                            if (flag == 1)
                            {
                                int now = i * 8 + j;
                                tei_str += now.ToString("X3") + "  ";
                                num++;
                                if (num >= 10)
                                {
                                    num = 0;
                                    heartBeatCheck.详细TEI.Add(tei_str);
                                    tei_str = "";
                                }
                            }
                        }
                    }
                }
                if (num != 0)
                {
                    num = 0;
                    heartBeatCheck.详细TEI.Add(tei_str);
                    tei_str = "";
                }
                //heartBeatCheck.resv = buf[start + 138];
            }
            else
            {
                detail += "原始源TEI:" + heartBeatCheck.原始源TEI;
                detail += "|发现站点数最大的站点TEI:" + heartBeatCheck.发现站点数最大的站点TEI;
                detail += "|最大的发现站点数:" + heartBeatCheck.最大的发现站点数;
            }
            return heartBeatCheck;
        }



        public static MMeDiscoverNodeList_s discoverNodeList_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 42))
            {
                return null;
            }
            int start_tmp = start;
            byte tmp;
            MMeDiscoverNodeList_s discoverNodeList = new MMeDiscoverNodeList_s();

            discoverNodeList.TEI = comFunc.ToUInt16(buf, start).ToString("X3");
            if (buf[start + 2] == 1)
            {
                discoverNodeList.角色 = "STA";
            }
            else if (buf[start + 2] == 2)
            {
                discoverNodeList.角色 = "PCO";
            }
            else if (buf[start + 2] == 4)
            {
                discoverNodeList.角色 = "CCO";
            }
            else
            {
                discoverNodeList.角色 = "未知";
            }
            discoverNodeList.站点层级 = buf[start + 3];
            Array.Copy(buf, start + 4, discoverNodeList.MAC地址, 0, 6);
            discoverNodeList.代理TEI = comFunc.ToUInt16(buf, start + 10).ToString("X3");
            Array.Copy(buf, start + 12, discoverNodeList.resv1, 0, 4);
            discoverNodeList.与代理站点通信成功率计算完成标记 = comFunc.BitField8(buf[start + 15], 7, 1);
            discoverNodeList.与代理站点通信成功率 = comFunc.ToUInt32(buf, start + 16);
            discoverNodeList.与代理站点下行通信成功率 = comFunc.ToUInt32(buf, start + 20);
            discoverNodeList.站点总数 = comFunc.ToUInt16(buf, start + 24);
            discoverNodeList.发送发现列表报文个数 = comFunc.ToUInt16(buf, start + 26);
            discoverNodeList.上行路由条目总数 = comFunc.ToUInt16(buf, start + 28);
            discoverNodeList.接收发现列表信息条目长度 = buf[start + 30];
            discoverNodeList.resv2 = comFunc.ToUInt16(buf, start + 31);
            discoverNodeList.路由周期到期剩余时间s = comFunc.ToUInt16(buf, start + 33);
            discoverNodeList.相线3 = comFunc.BitField8(buf[start + 35], 0, 2);
            discoverNodeList.相线2 = comFunc.BitField8(buf[start + 35], 2, 2);
            discoverNodeList.相线1 = comFunc.BitField8(buf[start + 35], 4, 2);
            discoverNodeList.resv3 = comFunc.BitField8(buf[start + 35], 6, 2);
            discoverNodeList.最小通信成功率 = buf[start + 36];
            Array.Copy(buf, start + 37, discoverNodeList.resv4, 0, 5);

            if (simple_flag == 1)
            {
                detail += "角色:" + discoverNodeList.角色;
                detail += "|层级:" + discoverNodeList.站点层级;
                detail += "|代理TEI:" + discoverNodeList.代理TEI;
                detail += "|与PCO通信成功率:" + discoverNodeList.与代理站点通信成功率;
                detail += "|站点总数:" + discoverNodeList.站点总数;
                detail += "|评估相线:" + discoverNodeList.相线3 + " " + discoverNodeList.相线2 + " " + discoverNodeList.相线1;
                detail += "|路由周期剩余时间:" + discoverNodeList.路由周期到期剩余时间s + "s";
                if (buf.Length < (start + 42 + discoverNodeList.上行路由条目总数 * 3))
                {
                    detail += "|上行条目总数有误";
                }
                return discoverNodeList;
            }

            start += 42;
            if (discoverNodeList.上行路由条目总数 != 0)
            {
                if (buf.Length < (start + discoverNodeList.上行路由条目总数 * 3))
                {
                    return discoverNodeList;
                }
                discoverNodeList.上行路由条目 = new byte[discoverNodeList.上行路由条目总数 * 3];
                Array.Copy(buf, start, discoverNodeList.上行路由条目, 0, discoverNodeList.上行路由条目.Length);
                discoverNodeList.上行路由信息字段 = new List<nulroute_info_c>();
                for (int i = 0; i < discoverNodeList.上行路由条目总数; i++)
                {
                    nulroute_info_c nulroute_info = new nulroute_info_c();
                    nulroute_info.下一跳TEI = comFunc.ToUInt16(discoverNodeList.上行路由条目, i * 3).ToString("X3");
                    tmp = discoverNodeList.上行路由条目[i * 3 + 2];
                    if (tmp == 0)
                    {
                        nulroute_info.路由类型 = "错误的路由";
                    }
                    else if (tmp == 1)
                    {
                        nulroute_info.路由类型 = "同级备份路由";
                    }
                    else if (tmp == 2)
                    {
                        nulroute_info.路由类型 = "上级备份路由";
                    }
                    else if (tmp == 3)
                    {
                        nulroute_info.路由类型 = "代理主路径路由";
                    }
                    else if (tmp == 4)
                    {
                        nulroute_info.路由类型 = "上上级路由";
                    }
                    else
                    {
                        nulroute_info.路由类型 = "未知类型";
                    }

                    discoverNodeList.上行路由信息字段.Add(nulroute_info);
                }
                start += discoverNodeList.上行路由条目.Length;
            }

            Array.Copy(buf, start, discoverNodeList.发现站点列表位图, 0, 128);
            start += 128;
            UInt16 tmp16 = 0;
            for(int i = 0; i < 128; i++)
            {
                for(int j = 0; j < 8; j++)
                {
                    if ((discoverNodeList.发现站点列表位图[i] & 1<<j) != 0)
                    {
                        if (start >= buf.Length)
                            break;
                        find_list_info_c find_list_info = new find_list_info_c();
                        tmp16 = (UInt16)(i * 8 + j);
                        discoverNodeList.接收发现列表信息 += "|"+ tmp16.ToString("X3") + ",";
                        discoverNodeList.接收发现列表信息 += buf[start++];
                    }
                }
            }

            discoverNodeList.原始数据 = new byte[start - start_tmp];
            Array.Copy(buf, start_tmp, discoverNodeList.原始数据, 0, discoverNodeList.原始数据.Length);

            return discoverNodeList;
        }

        /*国网发现列表解析*/
        public static MMeDiscoverNodeList_s_gw discoverNodeList_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 32))
            {
                return null;
            }
            int start_tmp = start;
            byte tmp;
            UInt32 tmp32;
            MMeDiscoverNodeList_s_gw discoverNodeList = new MMeDiscoverNodeList_s_gw();

            tmp32 = comFunc.ToUInt32(buf, start);
            discoverNodeList.TEI = comFunc.BitField32(tmp32,0,12).ToString("X3");
            discoverNodeList.代理TEI = comFunc.BitField32(tmp32, 12, 12).ToString("X3");

            if (comFunc.BitField8(buf[start + 3], 0, 4) == 1)
            {
                discoverNodeList.角色 = "STA";
            }
            else if (comFunc.BitField8(buf[start + 3], 0, 4) == 2)
            {
                discoverNodeList.角色 = "PCO";
            }
            else if (comFunc.BitField8(buf[start + 3], 0, 4) == 4)
            {
                discoverNodeList.角色 = "CCO";
            }
            else
            {
                discoverNodeList.角色 = "未知";
            }
            discoverNodeList.站点层级 = comFunc.BitField8(buf[start + 3], 4, 4);
            Array.Copy(buf, start + 4, discoverNodeList.MAC地址, 0, 6);
            
            Array.Copy(buf, start + 10, discoverNodeList.CCO_MAC地址, 0, 6);

            discoverNodeList.相线1 = comFunc.BitField8(buf[start + 16], 0, 2);
            discoverNodeList.相线2 = comFunc.BitField8(buf[start + 16], 2, 2);
            discoverNodeList.相线3 = comFunc.BitField8(buf[start + 16], 4, 2);
            discoverNodeList.resv1 = comFunc.BitField8(buf[start + 16], 6, 2);

            discoverNodeList.代理站点信道质量 = comFunc.BitField8(buf[start + 17], 0, 8);
            discoverNodeList.代理站点通信成功率 = buf[start + 18];
            discoverNodeList.代理站点下行通信成功率 = buf[start + 19];
            discoverNodeList.站点总数 = comFunc.ToUInt16(buf, start + 20);
            discoverNodeList.发送发现列表报文个数 = buf[start + 22];
            discoverNodeList.上行路由条目总数 = buf[start + 23];
            //discoverNodeList.接收发现列表信息条目长度 = buf[start + 30];
            //discoverNodeList.resv2 = comFunc.ToUInt16(buf, start + 31);
            discoverNodeList.路由周期到期剩余时间s = comFunc.ToUInt16(buf, start + 24);
            discoverNodeList.位图大小 = comFunc.ToUInt16(buf, start + 26);

            discoverNodeList.最小通信成功率 = buf[start + 28];
            Array.Copy(buf, start + 37, discoverNodeList.resv3, 0, 3);

            if (simple_flag == 1)
            {
                detail += "角色:" + discoverNodeList.角色;
                detail += "|层级:" + discoverNodeList.站点层级;
                detail += "|代理TEI:" + discoverNodeList.代理TEI;
                detail += "|与PCO通信成功率:" + discoverNodeList.代理站点通信成功率;
                detail += "|站点总数:" + discoverNodeList.站点总数;
                detail += "|评估相线:" + discoverNodeList.相线1 + " " + discoverNodeList.相线2 + " " + discoverNodeList.相线3;
                detail += "|路由周期剩余时间:" + discoverNodeList.路由周期到期剩余时间s + "s";
                if (buf.Length < (start + 32 + discoverNodeList.上行路由条目总数 * 3))
                {
                    detail += "|上行条目总数有误";
                }
                return discoverNodeList;
            }

            start += 32;
            if (discoverNodeList.上行路由条目总数 != 0)
            {
                if (buf.Length < (start + discoverNodeList.上行路由条目总数 * 3))
                {
                    return discoverNodeList;
                }
                discoverNodeList.上行路由条目 = new byte[discoverNodeList.上行路由条目总数 * 3];
                Array.Copy(buf, start, discoverNodeList.上行路由条目, 0, discoverNodeList.上行路由条目.Length);
                discoverNodeList.上行路由信息字段 = new List<nulroute_info_c>();
                for (int i = 0; i < discoverNodeList.上行路由条目总数; i++)
                {
                    nulroute_info_c nulroute_info = new nulroute_info_c();
                    nulroute_info.下一跳TEI = comFunc.ToUInt16(discoverNodeList.上行路由条目, i * 3).ToString("X3");
                    tmp = discoverNodeList.上行路由条目[i * 3 + 2];
                    if (tmp == 0)
                    {
                        nulroute_info.路由类型 = "错误的路由";
                    }
                    else if (tmp == 1)
                    {
                        nulroute_info.路由类型 = "同级备份路由";
                    }
                    else if (tmp == 2)
                    {
                        nulroute_info.路由类型 = "上级备份路由";
                    }
                    else if (tmp == 3)
                    {
                        nulroute_info.路由类型 = "代理主路径路由";
                    }
                    else if (tmp == 4)
                    {
                        nulroute_info.路由类型 = "上上级路由";
                    }
                    else
                    {
                        nulroute_info.路由类型 = "未知类型";
                    }

                    discoverNodeList.上行路由信息字段.Add(nulroute_info);
                }
                start += discoverNodeList.上行路由条目.Length;
            }

            discoverNodeList.发现站点列表位图 = new byte[buf.Length - start];
            Array.Copy(buf, start, discoverNodeList.发现站点列表位图, 0, discoverNodeList.发现站点列表位图.Length);
            start += discoverNodeList.发现站点列表位图.Length;
            UInt16 tmp16 = 0;
            for (int i = 0; i < discoverNodeList.发现站点列表位图.Length; i++)
            {
                for (int j = 0; j < 8; j++)
                {
                    if ((discoverNodeList.发现站点列表位图[i] & 1 << j) != 0)
                    {
                        if (start >= buf.Length)
                            break;
                        find_list_info_c find_list_info = new find_list_info_c();
                        tmp16 = (UInt16)(i * 8 + j);
                        discoverNodeList.接收发现列表信息 += "|" + tmp16.ToString("X3") + ",";
                        discoverNodeList.接收发现列表信息 += buf[start++];
                    }
                }
            }

            discoverNodeList.原始数据 = new byte[start - start_tmp];
            Array.Copy(buf, start_tmp, discoverNodeList.原始数据, 0, discoverNodeList.原始数据.Length);

            return discoverNodeList;
        }

        public static MMeSuccessRateReport_s successRateReport_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 4))
            {
                return null;
            }
            MMeSuccessRateReport_s successRateReport = new MMeSuccessRateReport_s();

            successRateReport.TEI = comFunc.ToUInt16(buf, start).ToString("X3");
            successRateReport.站点总数 = comFunc.ToUInt16(buf, start + 2);

            if(simple_flag != 1)
            {
                if (buf.Length < (start + 4 + successRateReport.站点总数 * 4))
                {
                    return successRateReport;
                }
                successRateReport.原始数据 = new byte[4 + successRateReport.站点总数 * 4];
                Array.Copy(buf, start, successRateReport.原始数据, 0, successRateReport.原始数据.Length);
                if (successRateReport.站点总数 != 0)
                {
                    successRateReport.通信成功率信息 = new List<substa_sr_c>();
                    for (int i = 0; i < successRateReport.站点总数; i++)
                    {
                        substa_sr_c substa_sr = new substa_sr_c();
                        substa_sr.子站点TEI = comFunc.ToUInt16(buf, start + 4 + i * 4).ToString("X3");
                        substa_sr.下行通信成功率 = buf[start + 4 + i * 4 + 2];
                        substa_sr.上行通信成功率 = buf[start + 4 + i * 4 + 3];
                        successRateReport.通信成功率信息.Add(substa_sr);
                    }
                }
            }
            else
            {
                detail += "TEI:" + successRateReport.TEI;
                detail += "|子站点总数:" + successRateReport.站点总数;
                if (buf.Length < (start + 4 + successRateReport.站点总数 * 4))
                {
                    detail += "|子站点总数有误";
                }
            }
            
            return successRateReport;
        }

        /*国网通信成功率上报*/
        public static MMeSuccessRateReport_s_gw successRateReport_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 4))
            {
                return null;
            }
            MMeSuccessRateReport_s_gw successRateReport = new MMeSuccessRateReport_s_gw();

            successRateReport.TEI = comFunc.ToUInt16(buf, start).ToString("X3");
            successRateReport.站点总数 = comFunc.ToUInt16(buf, start + 2);

            if (simple_flag != 1)
            {
                if (buf.Length < (start + 4 + successRateReport.站点总数 * 4))
                {
                    return successRateReport;
                }
                successRateReport.原始数据 = new byte[4 + successRateReport.站点总数 * 4];
                Array.Copy(buf, start, successRateReport.原始数据, 0, successRateReport.原始数据.Length);
                if (successRateReport.站点总数 != 0)
                {
                    successRateReport.通信成功率信息 = new List<substa_sr_c>();
                    for (int i = 0; i < successRateReport.站点总数; i++)
                    {
                        substa_sr_c substa_sr = new substa_sr_c();
                        substa_sr.子站点TEI = comFunc.ToUInt16(buf, start + 4 + i * 4).ToString("X3");
                        substa_sr.下行通信成功率 = buf[start + 4 + i * 4 + 2];
                        substa_sr.上行通信成功率 = buf[start + 4 + i * 4 + 3];
                        successRateReport.通信成功率信息.Add(substa_sr);
                    }
                }
            }
            else
            {
                detail += "TEI:" + successRateReport.TEI;
                detail += "|子站点总数:" + successRateReport.站点总数;
                if (buf.Length < (start + 4 + successRateReport.站点总数 * 4))
                {
                    detail += "|子站点总数有误";
                }
            }

            return successRateReport;
        }


        public static MMeNidRepeatReport_s nidRepeatReport_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 9))
            {
                return null;
            }
            UInt16 tmp16 = 0;
            MMeNidRepeatReport_s nidRepeatReport = new MMeNidRepeatReport_s();
            Array.Copy(buf, start, nidRepeatReport.CCO_MAC, 0, 6);
            nidRepeatReport.邻居网络个数 = buf[start + 6];
            tmp16 = comFunc.ToUInt16(buf, start + 7);
            for (int i = 1; i < 16; i++)
            {
                if (comFunc.BitField16(tmp16, i, 1) != 0)
                {
                    nidRepeatReport.邻居网络 += " " + i.ToString();
                }
            }

            if (simple_flag == 1)
            {
                detail += "|CCO[" + comFunc.ByteArryToHexStr_2(nidRepeatReport.CCO_MAC) + "]";
                detail += "|邻居网络个数" + nidRepeatReport.邻居网络个数;
                detail += "|邻居网络" + nidRepeatReport.邻居网络;
            }
            return nidRepeatReport;
        }


        /*国网网络冲突上报*/
        public static MMeNidRepeatReport_s_gw nidRepeatReport_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 7))
            {
                return null;
            }
            UInt32 tmp32 = 0;
            MMeNidRepeatReport_s_gw nidRepeatReport = new MMeNidRepeatReport_s_gw();
            Array.Copy(buf, start, nidRepeatReport.CCO_MAC, 0, 6);
            nidRepeatReport.邻居网络个数 = buf[start + 6];
            nidRepeatReport.网络号字节宽度 = buf[start + 7];//协议上说默认为3
            for (int i = 0; i < nidRepeatReport.邻居网络个数; i++)
            {
                tmp32 = comFunc.ToUInt24(buf, start + 8 + 3 * i);
                nidRepeatReport.邻居网络条目[i] = tmp32;
            }

            if (simple_flag == 1)
            {
                detail += "|CCO[" + comFunc.ByteArryToHexStr_2(nidRepeatReport.CCO_MAC) + "]";
                detail += "|邻居网络个数" + nidRepeatReport.邻居网络个数;
                // detail += "|邻居网络" + nidRepeatReport.邻居网络;
            }
            return nidRepeatReport;
        }

        public static MMeZeroCrossNTBCollectInd_s zeroCrossNTBCollectInd_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 5))
            {
                return null;
            }
            MMeZeroCrossNTBCollectInd_s zeroCrossNTBCollectInd = new MMeZeroCrossNTBCollectInd_s();

            if (simple_flag != 1)
            {
                Array.Copy(buf, start, zeroCrossNTBCollectInd.原始数据, 0, zeroCrossNTBCollectInd.原始数据.Length);
            }

            zeroCrossNTBCollectInd.站点TEI = comFunc.ToUInt16(buf, start).ToString("X3");
            if (buf[start + 2] == 0)
            {
                zeroCrossNTBCollectInd.站点类型 = "单站点";
            }
            else if (buf[start + 2] == 1)
            {
                zeroCrossNTBCollectInd.站点类型 = "全网站点";
            }
            else
            {
                zeroCrossNTBCollectInd.站点类型 =  buf[start + 2].ToString();
            }
            if (buf[start + 3] == 0)
            {
                zeroCrossNTBCollectInd.采集周期 = "半个电力线周期";
            }
            else if (buf[start + 3] == 1)
            {
                zeroCrossNTBCollectInd.采集周期 = "一个电力线周期";
            }
            else
            {
                zeroCrossNTBCollectInd.采集周期 = buf[start + 3].ToString();
            }
            zeroCrossNTBCollectInd.采集数量 = buf[start + 4];
            if (simple_flag == 1)
            {
                detail += "采集站点TEI:" + zeroCrossNTBCollectInd.站点TEI;
                detail += "|站点类型:" + zeroCrossNTBCollectInd.站点类型;
                detail += "|采集周期:" + zeroCrossNTBCollectInd.采集周期;
                detail += "|采集数量:" + zeroCrossNTBCollectInd.采集数量;
            }
                
            return zeroCrossNTBCollectInd;
        }

        /*国网过零NTB采集*/
        public static MMeZeroCrossNTBCollectInd_s_gw zeroCrossNTBCollectInd_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 8))
            {
                return null;
            }
            MMeZeroCrossNTBCollectInd_s_gw zeroCrossNTBCollectInd = new MMeZeroCrossNTBCollectInd_s_gw();

            if (simple_flag != 1)
            {
                Array.Copy(buf, start, zeroCrossNTBCollectInd.原始数据, 0, zeroCrossNTBCollectInd.原始数据.Length);
            }

            zeroCrossNTBCollectInd.站点TEI = comFunc.ToUInt16(buf, start).ToString("X3");
            if (buf[start + 2] == 0)
            {
                zeroCrossNTBCollectInd.站点类型 = "单站点";
            }
            else if (buf[start + 2] == 1)
            {
                zeroCrossNTBCollectInd.站点类型 = "全网站点";
            }
            else
            {
                zeroCrossNTBCollectInd.站点类型 = buf[start + 2].ToString();
            }
            if (buf[start + 3] == 0)
            {
                zeroCrossNTBCollectInd.采集周期 = "二分之一电力线周期";
            }
            else if (buf[start + 3] == 1)
            {
                zeroCrossNTBCollectInd.采集周期 = "一个电力线周期";
            }
            else
            {
                zeroCrossNTBCollectInd.采集周期 = buf[start + 3].ToString();
            }
            zeroCrossNTBCollectInd.采集数量 = buf[start + 4];
            if (simple_flag == 1)
            {
                detail += "采集站点TEI:" + zeroCrossNTBCollectInd.站点TEI;
                detail += "|站点类型:" + zeroCrossNTBCollectInd.站点类型;
                detail += "|采集周期:" + zeroCrossNTBCollectInd.采集周期;
                detail += "|采集数量:" + zeroCrossNTBCollectInd.采集数量;
            }

            return zeroCrossNTBCollectInd;
        }


        public static MMeZeroCrossNTBReport_s zeroCrossNTBReport_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 8))
            {
                return null;
            }
            MMeZeroCrossNTBReport_s zeroCrossNTBReport = new MMeZeroCrossNTBReport_s();

            zeroCrossNTBReport.站点TEI = comFunc.ToUInt16(buf, start).ToString("X3");
            zeroCrossNTBReport.上报数量 = buf[start + 2];
            zeroCrossNTBReport.resv = buf[start + 3];
            zeroCrossNTBReport.基准NTB值 = comFunc.ToUInt32(buf, start + 4);

            if (simple_flag != 1)
            {
                if (buf.Length < (start + 8 + (zeroCrossNTBReport.上报数量 * 12 / 8) + (zeroCrossNTBReport.上报数量 * 12 % 8)))
                {
                    return zeroCrossNTBReport;
                }
                zeroCrossNTBReport.原始数据 = new byte[8 + (zeroCrossNTBReport.上报数量 * 12 / 8) + (zeroCrossNTBReport.上报数量 * 12 % 8)];
                Array.Copy(buf, start, zeroCrossNTBReport.原始数据, 0, zeroCrossNTBReport.原始数据.Length);

                if (zeroCrossNTBReport.上报数量 != 0)
                {
                    zeroCrossNTBReport.过零NTB差值 = new ushort[zeroCrossNTBReport.上报数量-1];
                    zeroCrossNTBReport.过零NTB差值ms = new double[zeroCrossNTBReport.上报数量 - 1];
                    for (int i = 0; i < (zeroCrossNTBReport.上报数量 - 1); i++)
                    {
                        int idx = i / 2;
                        UInt32 tmp32 = comFunc.ToUInt32(buf, start + 8 + idx * 3);
                        tmp32 &= 0x00FFFFFF; //取三个字节
                        UInt32 ntb = comFunc.BitField32(tmp32, (i % 2 == 0 ? 0 : 12), 12);

                        zeroCrossNTBReport.过零NTB差值[i] = ((ushort)ntb);
                        ntb = ntb << 8;
                        zeroCrossNTBReport.过零NTB差值ms[i] = (double)(ntb * 0.04 / 1000);
                    }
                }
            }
            else
            {
                detail += "站点TEI:" + zeroCrossNTBReport.站点TEI;
                detail += "|上报数量:" + zeroCrossNTBReport.上报数量;
                detail += "|基准NTB值:" + zeroCrossNTBReport.基准NTB值;
                if (buf.Length < (start + 8 + (zeroCrossNTBReport.上报数量 * 12 / 8) + (zeroCrossNTBReport.上报数量 * 12 % 8)))
                {
                    detail += "|上报数量有误";
                }
            }
            return zeroCrossNTBReport;
        }


        public static MMeZeroCrossNTBReport_s_gw zeroCrossNTBReport_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 10))
            {
                return null;
            }
            MMeZeroCrossNTBReport_s_gw zeroCrossNTBReport = new MMeZeroCrossNTBReport_s_gw();

            UInt16 tmp16 = comFunc.ToUInt16(buf, start);
            zeroCrossNTBReport.TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
            zeroCrossNTBReport.告知总数量 = buf[start + 2];
            zeroCrossNTBReport.相线1差值告知数量 = buf[start + 3];
            zeroCrossNTBReport.相线2差值告知数量 = buf[start + 4];
            zeroCrossNTBReport.相线3差值告知数量 = buf[start + 5];
            //zeroCrossNTBReport.resv = buf[start + 3];
            zeroCrossNTBReport.基准NTB值 = comFunc.ToUInt32(buf, start + 6).ToString("X8");

            if (simple_flag != 1)
            {
                if (buf.Length < (start + 8 + (zeroCrossNTBReport.告知总数量 * 12 / 8) + (zeroCrossNTBReport.告知总数量 * 12 % 8)))
                {
                    return zeroCrossNTBReport;
                }
                zeroCrossNTBReport.原始数据 = new byte[8 + (zeroCrossNTBReport.告知总数量 * 12 / 8) + (zeroCrossNTBReport.告知总数量 * 12 % 8)];
                Array.Copy(buf, start, zeroCrossNTBReport.原始数据, 0, zeroCrossNTBReport.原始数据.Length);

                if (zeroCrossNTBReport.相线1差值告知数量 != 0)
                {
                    //zeroCrossNTBReport.过零NTB差值 = new ushort[zeroCrossNTBReport.告知总数量 - 1];
                    zeroCrossNTBReport.相线1过零NTB差值 = new string[zeroCrossNTBReport.相线1差值告知数量];
                    //zeroCrossNTBReport.过零NTB差值ms = new double[zeroCrossNTBReport.告知总数量 - 1];
                    for (int i = 0; i < (zeroCrossNTBReport.相线1差值告知数量 ); i++)
                    {
                        int idx = i / 2;
                        UInt32 tmp32 = comFunc.ToUInt32(buf, start + 10 + idx * 3);
                        tmp32 &= 0x00FFFFFF; //取三个字节
                        UInt32 ntb = comFunc.BitField32(tmp32, (i % 2 == 0 ? 0 : 12), 12);

                        zeroCrossNTBReport.相线1过零NTB差值[i] = ((ushort)ntb).ToString("X");


                        /* ntb = ntb >> 8;
                         * zeroCrossNTBReport.过零NTB差值ms[i] = (double)(ntb * 0.04 / 1000);*/
                    }
                }

                if (zeroCrossNTBReport.相线2差值告知数量 != 0)
                {
                    //zeroCrossNTBReport.过零NTB差值 = new ushort[zeroCrossNTBReport.告知总数量 - 1];
                    zeroCrossNTBReport.相线2过零NTB差值 = new ushort[zeroCrossNTBReport.告知总数量 ];
                    //zeroCrossNTBReport.过零NTB差值ms = new double[zeroCrossNTBReport.告知总数量 - 1];
                    for (int i = 0; i < (zeroCrossNTBReport.相线1差值告知数量 ); i++)
                    {
                        int idx = i / 2;
                        UInt32 tmp32 = comFunc.ToUInt32(buf, start + 9 + idx * 3);
                        tmp32 &= 0x00FFFFFF; //取三个字节
                        UInt32 ntb = comFunc.BitField32(tmp32, (i % 2 == 0 ? 0 : 12), 12);

                        zeroCrossNTBReport.相线2过零NTB差值[i] = ((ushort)ntb);
                        /*ntb = ntb << 8;
                        zeroCrossNTBReport.过零NTB差值ms[i] = (double)(ntb * 0.04 / 1000);*/
                    }
                }

                if (zeroCrossNTBReport.相线3差值告知数量 != 0)
                {
                    //zeroCrossNTBReport.过零NTB差值 = new ushort[zeroCrossNTBReport.告知总数量 - 1];
                    zeroCrossNTBReport.相线3过零NTB差值 = new ushort[zeroCrossNTBReport.告知总数量 ];
                    //zeroCrossNTBReport.过零NTB差值ms = new double[zeroCrossNTBReport.告知总数量 - 1];
                    for (int i = 0; i < (zeroCrossNTBReport.相线1差值告知数量 ); i++)
                    {
                        int idx = i / 2;
                        UInt32 tmp32 = comFunc.ToUInt32(buf, start + 9 + idx * 3);
                        tmp32 &= 0x00FFFFFF; //取三个字节
                        UInt32 ntb = comFunc.BitField32(tmp32, (i % 2 == 0 ? 0 : 12), 12);

                        zeroCrossNTBReport.相线3过零NTB差值[i] = ((ushort)ntb);
                        /*ntb = ntb << 8;
                        zeroCrossNTBReport.过零NTB差值ms[i] = (double)(ntb * 0.04 / 1000);*/
                    }
                }
            }
            else
            {
                detail += "站点TEI:" + zeroCrossNTBReport.TEI;
                detail += "|上报数量:" + zeroCrossNTBReport.告知总数量;
                detail += "|基准NTB值:" + zeroCrossNTBReport.基准NTB值;
                if (buf.Length < (start + 8 + (zeroCrossNTBReport.告知总数量 * 12 / 8) + (zeroCrossNTBReport.告知总数量 * 12 % 8)))
                {
                    detail += "|上报数量有误";
                }
            }
            return zeroCrossNTBReport;
        }

        public static MMeDiagnose_c diagnose_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            MMeDiagnose_c diagnose = new MMeDiagnose_c();
            UInt16 id = comFunc.ToUInt16(buf, start);
            if (id == 0x0001)
            {
                diagnose.芯片厂商ID = "HS";
            }
            else if (id == 0x0002)
            {
                diagnose.芯片厂商ID = "HS";
            }
            else if (id == 0x0003)
            {
                diagnose.芯片厂商ID = "HS";
            }
            else if (id == 0x0004)
            {
                diagnose.芯片厂商ID = "HS";
            }
            else if (id == 0x0005)
            {
                diagnose.芯片厂商ID = "HS";
            }
            else if (id == 0x0006)
            {
                diagnose.芯片厂商ID = "HS";
            }
            else if (id == 0x0007)
            {
                diagnose.芯片厂商ID = "HS";
            }
            else if (id == 0x0008)
            {
                diagnose.芯片厂商ID = "HS";
            }
            else
            {
                diagnose.芯片厂商ID = "保留[" + id.ToString("X2") + "]";
            }
            if (simple_flag == 1)
            {
                detail += "芯片厂商:" + diagnose.芯片厂商ID;
            }
            else
            {
                diagnose.厂家自定义 = new byte[bpsz - start - 2];
                Array.Copy(buf, start + 2, diagnose.厂家自定义, 0, diagnose.厂家自定义.Length);
            }

            return diagnose;

        }

        /*国网网络诊断*/
        public static MMeDiagnose_c_gw diagnose_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            MMeDiagnose_c_gw diagnose = new MMeDiagnose_c_gw();
            UInt16 id = comFunc.ToUInt16(buf, start);
            if (id == 0x0001)
            {
                diagnose.芯片厂商ID = "HS";
            }
            else if (id == 0x0002)
            {
                diagnose.芯片厂商ID = "ES";
            }
            else if (id == 0x0003)
            {
                diagnose.芯片厂商ID = "TC";
            }
            else if (id == 0x0004)
            {
                diagnose.芯片厂商ID = "LH";
            }
            else if (id == 0x0005)
            {
                diagnose.芯片厂商ID = "HT";
            }
            else if (id == 0x0006)
            {
                diagnose.芯片厂商ID = "RS";
            }
            else if (id == 0x0007)
            {
                diagnose.芯片厂商ID = "SW";
            }
            else if (id == 0x0008)
            {
                diagnose.芯片厂商ID = "SC";
            }
            else if (id == 0x0009)
            {
                diagnose.芯片厂商ID = "YM";
            }
            else if (id == 0x000A)
            {
                diagnose.芯片厂商ID = "QJ";
            }
            else if (id == 0x000B)
            {
                diagnose.芯片厂商ID = "HZ";
            }
            else if (id == 0x000C)
            {
                diagnose.芯片厂商ID = "ZC";
            }
            else if (id == 0x000D)
            {
                diagnose.芯片厂商ID = "SP";
            }
            else if (id == 0x000E)
            {
                diagnose.芯片厂商ID = "PE";
            }
            else if (id == 0x000F)
            {
                diagnose.芯片厂商ID = "NR";
            }
            else if (id == 0x0010)
            {
                diagnose.芯片厂商ID = "SL";
            }
            else if (id == 0x0011)
            {
                diagnose.芯片厂商ID = "MT";
            }
            else if (id == 0x0012)
            {
                diagnose.芯片厂商ID = "SI";
            }
            else
            {
                diagnose.芯片厂商ID = "保留[" + id.ToString("X2") + "]";
            }
            if (simple_flag == 1)
            {
                detail += "芯片厂商:" + diagnose.芯片厂商ID;
            }
            else
            {
                diagnose.厂家自定义 = new byte[bpsz - start - 2];
                Array.Copy(buf, start + 2, diagnose.厂家自定义, 0, diagnose.厂家自定义.Length);
            }

            return diagnose;

        }


        /*国网路由请求*/
        public static MMeRouteRequest_gw routeRequest_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 7))
            {
                return null;
            }
            UInt16 tmp16 = 0;
            MMeRouteRequest_gw routeRequest = new MMeRouteRequest_gw();

            routeRequest.版本 = buf[start + 0];
            routeRequest.路由请求序列号 = comFunc.ToUInt32(buf, start + 1);
            routeRequest.路径优选标志 = comFunc.BitField8(buf[start + 5], 3, 1);
            if (comFunc.BitField8(buf[start + 5], 4, 4) == 0)
            {
                routeRequest.负载数据类型 = "未携带负载数据";
            }
            else if (comFunc.BitField8(buf[start + 5], 4, 4) == 1)
            {
                routeRequest.负载数据类型 = "传播路径列表";
            }
            else
            {
                routeRequest.负载数据类型 = "保留";
            }

            routeRequest.负载数据长度 = buf[start + 6];

            if (routeRequest.负载数据类型 == "传播路径列表")
            {
                if (buf.Length < (start + 7 + routeRequest.负载数据长度))
                {
                    return routeRequest;
                }
                routeRequest.原始数据 = new byte[7 + routeRequest.负载数据长度];
                Array.Copy(buf, start, routeRequest.原始数据, 0, routeRequest.原始数据.Length);
                start += 7;

                if (routeRequest.负载数据长度 != 0)
                {
                    routeRequest.负载数据 = new byte[routeRequest.负载数据长度];
                    Array.Copy(buf, start, routeRequest.负载数据, 0, routeRequest.负载数据.Length);
                    routeRequest.传播路径列表 = new List<string>();
                    for (int i = 0; i < routeRequest.负载数据长度; i++)
                    {
                        string sta_info = "";
                        tmp16 = comFunc.ToUInt16(routeRequest.负载数据, 4 * i);
                        sta_info = "  TEI[" + comFunc.BitField16(tmp16, 0, 12).ToString("X2") + "]";
                        sta_info += $"  通讯成功率{i}[" + routeRequest.负载数据[2 + 4 * i] + "]";
                        sta_info += $"  信道质量{i}[" + routeRequest.负载数据[3 + 4 * i] + "]";
                        routeRequest.传播路径列表.Add(sta_info);
                    }
                }

            }
            else
            {
                detail += "版本:" + routeRequest.版本;
                detail += "|路由请求序列号:" + routeRequest.路由请求序列号.ToString();
                detail += "|路径优选标志:" + routeRequest.路径优选标志;
                detail += "|负载数据类型:" + routeRequest.负载数据类型;


                if (buf.Length < (start + 7 + routeRequest.负载数据长度))
                {
                    detail += "|站点信息长度有误";
                }
            }
            return routeRequest;
        }

        /*国网路由回复*/
        public static MMeRouteReply_gw routeReply_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 7))
            {
                return null;
            }
            UInt16 tmp16 = 0;
            MMeRouteReply_gw RouteReply = new MMeRouteReply_gw();

            RouteReply.版本 = buf[start + 0];
            RouteReply.路由请求序列号 = comFunc.ToUInt32(buf, start + 1);
            if (comFunc.BitField8(buf[start + 5], 4, 4) == 0)
            {
                RouteReply.负载数据类型 = "未携带负载数据";
            }
            else if (comFunc.BitField8(buf[start + 5], 4, 4) == 1)
            {
                RouteReply.负载数据类型 = "传播路径列表";
            }
            else
            {
                RouteReply.负载数据类型 = "保留";
            }

            RouteReply.负载数据长度 = buf[start + 6];

            if (RouteReply.负载数据类型 == "传播路径列表")
            {
                if (buf.Length < (start + 7 + RouteReply.负载数据长度))
                {
                    return RouteReply;
                }
                RouteReply.原始数据 = new byte[7 + RouteReply.负载数据长度];
                Array.Copy(buf, start, RouteReply.原始数据, 0, RouteReply.原始数据.Length);
                start += 7;

                if (RouteReply.负载数据长度 != 0)
                {
                    RouteReply.负载数据 = new byte[RouteReply.负载数据长度];
                    Array.Copy(buf, start, RouteReply.负载数据, 0, RouteReply.负载数据.Length);
                    RouteReply.传播路径列表 = new List<string>();
                    for (int i = 0; i < RouteReply.负载数据长度; i++)
                    {
                        string sta_info = "";
                        tmp16 = comFunc.ToUInt16(RouteReply.负载数据, 4 * i);
                        sta_info = "  TEI[" + comFunc.BitField16(tmp16, 0, 12).ToString("X2") + "]";
                        sta_info += $"  通讯成功率{i}[" + RouteReply.负载数据[2 + 4 * i] + "]";
                        sta_info += $"  信道质量{i}[" + RouteReply.负载数据[3 + 4 * i] + "]";
                        RouteReply.传播路径列表.Add(sta_info);
                    }
                }

            }
            else
            {
                detail += "版本:" + RouteReply.版本;
                detail += "|路由请求序列号:" + RouteReply.路由请求序列号.ToString();
                detail += "|负载数据类型:" + RouteReply.负载数据类型;


                if (buf.Length < (start + 7 + RouteReply.负载数据长度))
                {
                    detail += "|站点信息长度有误";
                }
            }
            return RouteReply;
        }


        /*国网路由错误*/
        public static MMeRouteError_gw routeError_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 7))
            {
                return null;
            }

            MMeRouteError_gw RouteError = new MMeRouteError_gw();

            RouteError.版本 = buf[start + 0];
            RouteError.路由请求序列号 = comFunc.ToUInt32(buf, start + 1);
            RouteError.不可达站点数量 = buf[start + 6];

            if (simple_flag != 1)
            {
                if (buf.Length < (start + 7 + RouteError.不可达站点数量))
                {
                    return RouteError;
                }
                RouteError.原始数据 = new byte[7 + RouteError.不可达站点数量];
                Array.Copy(buf, start, RouteError.原始数据, 0, RouteError.原始数据.Length);
                start += 7;

                if (RouteError.不可达站点数量 != 0)
                {
                    RouteError.不可达站点 = new byte[RouteError.不可达站点数量];
                    Array.Copy(buf, start, RouteError.不可达站点, 0, RouteError.不可达站点.Length);
                    RouteError.不可达站点列表 = new List<MMeRouteError_TEI>();
                    for (int i = 0; i < RouteError.不可达站点数量; i++)
                    {
                        MMeRouteError_TEI sta_info = new MMeRouteError_TEI();
                        sta_info.TEI = comFunc.ToUInt16(RouteError.不可达站点, i * 2);
                        RouteError.不可达站点列表.Add(sta_info);
                    }
                }
            }
            else
            {
                detail += "版本:" + RouteError.版本;
                detail += "|路由请求序列号:" + RouteError.路由请求序列号.ToString();



                if (buf.Length < (start + 7 + RouteError.不可达站点数量))
                {
                    detail += "|站点信息长度有误";
                }
            }
            return RouteError;
        }

        /*国网路由应答*/
        public static MMeRouteAck_gw routeAck_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 7))
            {
                return null;
            }

            MMeRouteAck_gw RouteAck = new MMeRouteAck_gw();

            RouteAck.版本 = buf[start + 0];
            RouteAck.路由请求序列号 = comFunc.ToUInt32(buf, start + 4);

            return RouteAck;
        }


        /*国网链路确认请求*/
        public static MMeLinkConfirmRequest_gw linkConfirmRequest_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 7))
            {
                return null;
            }

            MMeLinkConfirmRequest_gw LinkConfirmRequest = new MMeLinkConfirmRequest_gw();

            LinkConfirmRequest.版本 = buf[start + 0];
            LinkConfirmRequest.路由请求序列号 = comFunc.ToUInt32(buf, start + 1);
            LinkConfirmRequest.确认站点数量 = buf[start + 6];

            if (simple_flag != 1)
            {
                if (buf.Length < (start + 7 + LinkConfirmRequest.确认站点数量))
                {
                    return LinkConfirmRequest;
                }
                LinkConfirmRequest.原始数据 = new byte[7 + LinkConfirmRequest.确认站点数量];
                Array.Copy(buf, start, LinkConfirmRequest.原始数据, 0, LinkConfirmRequest.原始数据.Length);
                start += 7;

                if (LinkConfirmRequest.确认站点数量 != 0)
                {
                    LinkConfirmRequest.确认站点 = new byte[LinkConfirmRequest.确认站点数量];
                    Array.Copy(buf, start, LinkConfirmRequest.确认站点, 0, LinkConfirmRequest.确认站点.Length);
                    LinkConfirmRequest.确认站点列表 = new List<MMeLinkConfirmRequest_TEI>();
                    for (int i = 0; i < LinkConfirmRequest.确认站点数量; i++)
                    {
                        MMeLinkConfirmRequest_TEI sta_info = new MMeLinkConfirmRequest_TEI();
                        sta_info.TEI = comFunc.ToUInt16(LinkConfirmRequest.确认站点, i * 2);
                        LinkConfirmRequest.确认站点列表.Add(sta_info);
                    }
                }
            }
            else
            {
                detail += "版本:" + LinkConfirmRequest.版本;
                detail += "|路由请求序列号:" + LinkConfirmRequest.路由请求序列号.ToString();



                if (buf.Length < (start + 7 + LinkConfirmRequest.确认站点数量))
                {
                    detail += "|站点信息长度有误";
                }
            }
            return LinkConfirmRequest;
        }

        /*国网链路确认回应*/
        public static MMeLinkConfirmResponse_gw linkConfirmResponse_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 7))
            {
                return null;
            }

            MMeLinkConfirmResponse_gw LinkConfirmResponse = new MMeLinkConfirmResponse_gw();

            LinkConfirmResponse.版本 = buf[start + 0];
            LinkConfirmResponse.层级 = buf[start + 1];
            LinkConfirmResponse.信道质量 = buf[start + 2];
            LinkConfirmResponse.路径优选标志 = comFunc.BitField8(buf[start + 3], 0, 1);
            LinkConfirmResponse.路由请求序列号 = comFunc.ToUInt32(buf, start + 1);

            detail += "|版本:" + LinkConfirmResponse.版本;
            detail += "|层级:" + LinkConfirmResponse.层级;
            detail += "|信道质量:" + LinkConfirmResponse.信道质量;
            detail += "|路径优选标志:" + LinkConfirmResponse.路径优选标志;
            detail += "|路由请求序列号:" + LinkConfirmResponse.路由请求序列号.ToString();

            if (buf.Length < (start + 7))
            {
                detail += "|站点信息长度有误";
            }

            return LinkConfirmResponse;
        }


        public static MMeRfConflictRpt_c rfConflictRpt_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 7))
            {
                return null;
            }
            MMeRfConflictRpt_c rfConflictRpt = new MMeRfConflictRpt_c();

            Array.Copy(buf, start, rfConflictRpt.CCO_MAC, 0, 6);
            rfConflictRpt.邻居网络个数 = buf[start + 6];
            if (simple_flag != 1)
            {
                if (buf.Length < (start + 7 + 2*rfConflictRpt.邻居网络个数))
                {
                    return rfConflictRpt;
                }
                rfConflictRpt.原始数据 = new byte[7 + 2 * rfConflictRpt.邻居网络个数];
                Array.Copy(buf, start, rfConflictRpt.原始数据, 0, 7 + 2* rfConflictRpt.邻居网络个数);
                if (rfConflictRpt.邻居网络个数 != 0)
                {
                    rfConflictRpt.邻居网络条目 = new List<string>();
                    for (int i = 0; i < rfConflictRpt.邻居网络个数; i++)
                    {
                        string item = "";
                        item += "信道号:" + rfConflictRpt.原始数据[7 + i * 2];
                        item += "|option:" + comFunc.BitField8(rfConflictRpt.原始数据[7 + i * 2 + 1], 0, 2);
                        item += "|保留:" + comFunc.BitField8(rfConflictRpt.原始数据[7 + i * 2 + 1], 2, 6);
                        rfConflictRpt.邻居网络条目.Add(item);
                    }
                }
            }
            else
            {
                detail += "|冲突的CCO地址[" + comFunc.ByteArryToHexStr(rfConflictRpt.CCO_MAC) + "]";
                detail += "|邻居网络个数" + rfConflictRpt.邻居网络个数;
                if (buf.Length < (start + 7 + 2*rfConflictRpt.邻居网络个数))
                {
                    detail += "|邻居网络个数有误";
                }
            }
            return rfConflictRpt;

        }


        public static MMeCltDataRpt_c cltDataRpt_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            if (buf.Length < (start + 4))
            {
                return null;
            }
            MMeCltDataRpt_c cltDataRpt = new MMeCltDataRpt_c();

            UInt16 tmp16 = 0;
            tmp16 = comFunc.ToUInt16(buf, start);
            cltDataRpt.TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
            cltDataRpt.resv = (byte)comFunc.BitField16(tmp16, 12, 4);
            cltDataRpt.报文序号 = buf[start + 2];
            cltDataRpt.采集上报报文总数 = buf[start + 3];

            if (simple_flag != 1)
            {
                cltDataRpt.原始数据 = new byte[buf.Length - start];
                Array.Copy(buf, start, cltDataRpt.原始数据, 0, cltDataRpt.原始数据.Length);
            }
            else
            {
                detail += "|TEI[" + cltDataRpt.TEI + "]";
                detail += "|报文序号" + cltDataRpt.报文序号;
                detail += "|采集上报报文总数" + cltDataRpt.采集上报报文总数;
                
            }
            return cltDataRpt;

        }

        public static MMeRfDiscoverList_c rfDiscoverList_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            byte tmp;
            UInt16 tmp16;
            MMeRfDiscoverList_c rfDiscoverList = new MMeRfDiscoverList_c();
            Array.Copy(buf, start, rfDiscoverList.站点MAC地址, 0, 6);
            rfDiscoverList.统计序号 = buf[start + 6];

            if (simple_flag == 1)
            {
                detail += "统计序号:" + rfDiscoverList.统计序号;
            }
            else
            {
                rfDiscoverList.原始数据 = new byte[bpsz - 4];
                Array.Copy(buf, start, rfDiscoverList.原始数据, 0, rfDiscoverList.原始数据.Length);
            }

            int idx = start + 7;
            int remain_len = comFunc.ToUInt16(buf, 2) - 7;

            rfDiscoverList.信息单元 = new List<rfdl_info_c>();
            while (remain_len > 0)
            {
                if (remain_len < 4)
                    break;

                byte info_type = comFunc.BitField8(buf[idx], 0, 7);
                byte len_type = comFunc.BitField8(buf[idx], 7, 1);
                UInt16 info_len = 0;
                idx++;
                remain_len--;

                if (len_type == 0)
                {
                    info_len = buf[idx];
                    idx++;
                    remain_len--;
                }
                else
                {
                    info_len = comFunc.ToUInt16(buf, idx);
                    idx += 2;
                    remain_len -= 2;
                }

                if (remain_len < info_len)
                    break;

                if (simple_flag == 1 && info_type == 0)
                {
                    byte[] cco = new byte[6];
                    Array.Copy(buf, idx, cco, 0, 6);
                    detail += "|CCO[" + comFunc.ByteArryToHexStr_2(cco) + "]";
                    tmp16 = comFunc.ToUInt16(buf, idx + 6);
                    detail += "|PCO:" + comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                    tmp = (byte)comFunc.BitField16(tmp16, 12, 4);
                    detail += "|角色:";
                    if (tmp == 4)
                    {
                        detail += "CCO";
                    }
                    else if (tmp == 2)
                    {
                        detail += "PCO";
                    }
                    else if (tmp == 1)
                    {
                        detail += "STA";
                    }
                    else
                    {
                        detail += tmp;
                    }
                    detail += "|层级" + comFunc.BitField8(buf[idx+8], 0, 4);
                    detail += "|链路RF跳数" + comFunc.BitField8(buf[idx + 8], 4, 4);
                    detail += "|上行接收率" + buf[idx + 9];
                    detail += "|下行接收率" + buf[idx + 10];
                    detail += "|链路最小接收率" + buf[idx + 11];
                }
               
                if (simple_flag == 1)
                {
                    idx += info_len;
                    remain_len -= info_len;
                    continue;
                }

                rfdl_info_c rfdl_info = new rfdl_info_c();
                rfdl_info.数据 = new byte[info_len];
                Array.Copy(buf, idx, rfdl_info.数据, 0, info_len);
                rfdl_info.长度类型 = len_type == 0 ? "1字节" : "2字节";
                rfdl_info.长度 = info_len;
                switch (info_type)
                {
                    case 0:
                        rfdl_sta_info_c rfdl_sta_info = new rfdl_sta_info_c();
                        Array.Copy(buf, idx, rfdl_sta_info.CCO_MAC, 0, 6);
                        tmp16 = comFunc.ToUInt16(buf, idx + 6);
                        rfdl_sta_info.PCO = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                        tmp = (byte)comFunc.BitField16(tmp16, 12, 4);
                        if (tmp == 4)
                        {
                            rfdl_sta_info.角色 += "CCO";
                        }
                        else if (tmp == 2)
                        {
                            rfdl_sta_info.角色 += "PCO";
                        }
                        else if (tmp == 1)
                        {
                            rfdl_sta_info.角色 += "STA";
                        }
                        else
                        {
                            rfdl_sta_info.角色 += tmp;
                        }
                        rfdl_sta_info.层级 = comFunc.BitField8(buf[idx + 8], 0, 4);
                        rfdl_sta_info.链路RF跳数 = comFunc.BitField8(buf[idx + 8], 4, 4);
                        rfdl_sta_info.代理上行接收率 = buf[idx + 9];
                        rfdl_sta_info.代理下行接收率 =  buf[idx + 10];
                        rfdl_sta_info.链路最小接收率 = buf[idx + 11];
                        rfdl_sta_info.无线发现列表周期 = buf[idx + 12];
                        rfdl_sta_info.无线接收率老化周期个数 = buf[idx + 13];
                        rfdl_info.类型 = "站点属性信息";
                        rfdl_info.内容 = (Object)rfdl_sta_info;
                        break;
                    case 1:
                        rfdl_rt_info_c rt_info = new rfdl_rt_info_c();
                        rfdl_info.类型 = "站点路由信息";
                        rt_info.下一跳站点 = new List<string>();
                        for (int i = 0; i < (UInt16)(info_len / 2);i++)
                        {
                            string next_info = "";
                            tmp16 = comFunc.ToUInt16(buf, idx + i * 2);
                            next_info += "TEI " + comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                            tmp16 = comFunc.BitField16(tmp16, 12, 4);
                            next_info += "  路由类型:";
                            if (tmp16 == 0)
                            {
                                next_info += "错误的路由";
                            }
                            else if (tmp16 == 1)
                            {
                                next_info += "同级备份路由";
                            }
                            else if (tmp16 == 2)
                            {
                                next_info += "上级备份路由";
                            }
                            else if (tmp16 == 3)
                            {
                                next_info += "代理主路径路由";
                            }
                            else if (tmp16 == 4)
                            {
                                next_info += "上上级路由";
                            }
                            rt_info.下一跳站点.Add(next_info);
                        }
                        rfdl_info.内容 = (Object)rt_info;
                        break;
                    case 2:
                        rfdl_info.类型 = "邻居节点信道信息非位图格式";
                        rfdl_info.内容 = (Object)rfdl_nb_chnl_info_deal(buf, info_len, idx);
                        break;
                    case 3:
                        rfdl_info.类型 = "邻居节点信道信息位图格式";
                        rfdl_info.内容 = (Object)rfdl_nb_chnl_info_bitmap_deal(buf, info_len, idx);
                        break;
                    default:
                        break;
                }
                rfDiscoverList.信息单元.Add(rfdl_info);
                idx += info_len;
                remain_len -= info_len;
            }
            return rfDiscoverList;
        }

        public static string snr_info_get(byte pos)
        {
            string[] snr_4bit =
            {
                "-7dB", "-5dB", "dB", "-1dB" ,
                "1dB", "3dB", "5dB", "7dB" , "9dB" ,
                "11dB", "13dB", "15dB", "18dB" , "21dB" ,
                "24dB" , "27dB"
            };

            if (pos > 15)
            {
                return "";
            }

            return snr_4bit[pos];
        }

        public static string rssi_info_get(int bit_type, UInt16 pos)
        {
#if false
            string[] rssi_4bit =
            {
                "RSSI<-105 dBm", "-105 dBm≤RSSI≤-95 dBm", "-95 dBm<RSSI≤-86 dBm", "-86 dBm<RSSI≤-80 dBm" ,
                "-80 dBm <RSSI≤-74 dBm", "-74 dBm <RSSI≤-68 dBm", "-68 dBm <RSSI≤-62 dBm", "-62 dBm <RSSI≤-56 dBm" ,
                "-56 dBm <RSSI≤-48 dBm", "-48 dBm <RSSI≤-42 dBm", "-42 dBm <RSSI≤-36 dBm", "-36 dBm <RSSI≤-30 dBm",
                "-30 dBm <RSSI≤-20 dBm", "-20 dBm <RSSI≤-10 dBm", "-10 dBm <RSSI≤0 dBm", "RSSI≥0 dBm"
            };
#else
            string[] rssi_4bit =
            {
                "-105 dBm", "-100dBm", "-90dBm", "-83dBm" ,
                "-77dBm", "-71dBm", "-65dBm", "-59dBm" ,
                "-52dBm", "-45dBm", "-39dBm", "-33dBm",
                "-25dBm", "-15dBm", "-5dBm", "2dBm"
            };
#endif
            string[] rssi_6bit =
            {
                "RSSI<-110 dBm", "-110 dBm≤RSSI≤-108 dBm", "-108 dBm<RSSI≤-106 dBm", "-106 dBm<RSSI≤-104 dBm",
                "-104 dBm <RSSI≤-102 dBm", "-102 dBm <RSSI≤-100 dBm", "-100 dBm <RSSI≤-98 dBm", "-98 dBm <RSSI≤-96 dBm",
                "-96 dBm <RSSI≤-94 dBm", "-94 dBm <RSSI≤-92 dBm", "-92 dBm <RSSI≤-90 dBm", "-90 dBm <RSSI≤-88 dBm",
                "-88 dBm <RSSI≤-86 dBm", "-86 dBm <RSSI≤-84 dBm", "-84 dBm <RSSI≤-82 dBm", "-82 dBm <RSSI≤-80 dBm",
                "-80 dBm <RSSI≤-78 dBm", "-78 dBm <RSSI≤-76 dBm", "-76 dBm <RSSI≤-74 dBm", "-74 dBm <RSSI≤-72 dBm",
                "-72 dBm <RSSI≤-70 dBm", "-70 dBm <RSSI≤-68 dBm", "-68 dBm <RSSI≤-66 dBm", "-66 dBm <RSSI≤-64 dBm",
                "-64 dBm <RSSI≤-62 dBm", "-62 dBm <RSSI≤-60 dBm", "-60 dBm <RSSI≤-58 dBm", "-58 dBm <RSSI≤-56 dBm",
                "-56 dBm <RSSI≤-54 dBm", "-54 dBm <RSSI≤-52 dBm", "-52 dBm <RSSI≤-50 dBm", "-50 dBm <RSSI≤-48 dBm",
                "-48 dBm <RSSI≤-46 dBm", "-46 dBm <RSSI≤-44 dBm", "-44 dBm <RSSI≤-42 dBm", "-42 dBm <RSSI≤-40 dBm",
                "-40 dBm <RSSI≤-38 dBm", "-38 dBm <RSSI≤-36 dBm", "-36 dBm <RSSI≤-34 dBm", "-34 dBm <RSSI≤-32 dBm",
                "-32 dBm <RSSI≤-30 dBm", "-30 dBm <RSSI≤-28 dBm", "-28 dBm <RSSI≤-26 dBm", "-26 dBm <RSSI≤-24 dBm",
                "-24 dBm <RSSI≤-22 dBm", "-22 dBm <RSSI≤-20 dBm", "-20 dBm <RSSI≤-18 dBm", "-18 dBm <RSSI≤-16 dBm",
                "-16 dBm <RSSI≤-14 dBm", "-14 dBm <RSSI≤-12 dBm", "-12 dBm <RSSI≤-10 dBm", "-10 dBm <RSSI≤-8 dBm",
                "-8 dBm <RSSI≤-6 dBm", "-6 dBm <RSSI≤-4 dBm", "-4 dBm <RSSI≤-2 dBm", "-2 dBm <RSSI≤0 dBm",
                "0 dBm <RSSI≤2 dBm", "2 dBm <RSSI≤4 dBm", "4 dBm <RSSI≤6 dBm", "6 dBm <RSSI≤8 dBm",
                "8 dBm <RSSI≤10 dBm", "10 dBm <RSSI≤12 dBm", "12 dBm <RSSI≤14 dBm", "RSSI≥14 dBm"

            };

            if (bit_type == 0 && pos < rssi_4bit.Length)
            {
                return rssi_4bit[pos];
            }
            else if (bit_type == 1 && pos < rssi_6bit.Length)
            {
                //return rssi_6bit[pos];
                return "" + (-111 + 2 * pos) + "dBm";
            }
            return null;
        }

        public static rfdl_nb_chnl_info_c rfdl_nb_chnl_info_deal(byte[] buf, int info_len, int start)
        {
            rfdl_nb_chnl_info_c rfdl_nb_chnl_info = new rfdl_nb_chnl_info_c();
            byte type = comFunc.BitField8(buf[start], 0, 4);
            UInt16 num = 0;
            UInt16 tmp16;
            UInt32 tmp32 = 0;
            string info_comb = "";

            if (info_len < 1)
            {
                return rfdl_nb_chnl_info;
            }

            if (type == 0)
            {
                rfdl_nb_chnl_info.信道信息组合类型 = "TEI长度12bit | 接收率长度8bit | 平均信噪比0bit | 信号强度0bit";
            }
            else if (type == 1)
            {
                rfdl_nb_chnl_info.信道信息组合类型 = "TEI长度12bit | 接收率长度8bit | 平均信噪比4bit | 信号强度0bit";
            }
            else if (type == 2)
            {
                rfdl_nb_chnl_info.信道信息组合类型 = "TEI长度12bit | 接收率长度8bit | 平均信噪比6bit | 信号强度6bit";
            }
            else if (type == 3)
            {
                rfdl_nb_chnl_info.信道信息组合类型 = "TEI长度12bit | 接收率长度8bit | 平均信噪比0bit | 信号强度4bit";
            }
            else
            {
                return rfdl_nb_chnl_info;
            }
            rfdl_nb_chnl_info.resv = comFunc.BitField8(buf[start], 4, 4);
            rfdl_nb_chnl_info.信道信息 = new List<string>();
            int index = start;
            info_len--;
            index++;
            if (type == 0)
            {
                num = (UInt16)(info_len * 8 / (12 + 8)); //一共有这么多个
                for (int i = 0; i < (UInt16)num/2; i++)
                {
                    if (info_len < 5)
                        break; 

                    tmp32 = comFunc.ToUInt32(buf, index);
                    info_comb = "TEI" + comFunc.BitField32(tmp32, 0, 12).ToString("X3");
                    info_comb += "  接收率" + comFunc.BitField32(tmp32, 12, 8).ToString();
                    rfdl_nb_chnl_info.信道信息.Add(info_comb);

                    info_comb = "TEI" + comFunc.BitField32(tmp32, 24, 12).ToString("X3");
                    info_comb += "  接收率" + buf[index + 4];
                    rfdl_nb_chnl_info.信道信息.Add(info_comb);

                    info_len -= 5;
                    index += 5;
                }

                if ((num & 1) != 0 && info_len > 3)
                {
                    tmp16 = comFunc.ToUInt16(buf, index);
                    info_comb = "TEI" + comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                    tmp16 = comFunc.ToUInt16(buf, index+1);
                    info_comb += "  接收率" + comFunc.BitField32(tmp32, 4, 8).ToString();
                    rfdl_nb_chnl_info.信道信息.Add(info_comb);
                }
            }

            else if (type == 1)
            {
                num = (UInt16)(info_len / 3); //一共有这么多个
                for (int i = 0; i < num; i++)
                {
                    if (info_len < 3)
                        break;

                    tmp16 = comFunc.ToUInt16(buf, index);
                    info_comb = "TEI" + comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                    tmp16 = comFunc.ToUInt16(buf, index+1);
                    info_comb += "  接收率" + comFunc.BitField16(tmp16, 4, 8).ToString();
                    info_comb += "  平均信噪比" + snr_info_get((byte)comFunc.BitField16(tmp16, 12, 4)); 
                    rfdl_nb_chnl_info.信道信息.Add(info_comb); 

                    info_len -= 3;
                    index += 3;
                }
            }

            else if (type == 2)
            {
                num = (UInt16)(info_len / 4); //一共有这么多个
                for (int i = 0; i < num; i++)
                {
                    if (info_len < 4)
                        break;

                    tmp32= comFunc.ToUInt32(buf, index);
                    info_comb = "TEI[" + comFunc.BitField32(tmp32, 0, 12).ToString("X3")+"]";
                    info_comb += "  接收率" + comFunc.BitField32(tmp32, 12, 8).ToString();
                    if (comFunc.BitField32(tmp32, 20, 6) == 0)
                    {
                        info_comb += "  平均信噪比SNR<-6 dB";
                    }
                    else
                    {
                        info_comb += "  平均信噪比SNR=" + ((int)comFunc.BitField32(tmp32, 20, 6) - 7) + "dB";
                    }
                    info_comb += "  信号强度" + rssi_info_get(1, (UInt16)comFunc.BitField32(tmp32, 26, 6));
                    rfdl_nb_chnl_info.信道信息.Add(info_comb);

                    info_len -= 4;
                    index += 4;
                }
            }


            else if (type == 3)
            {
                num = (UInt16)(info_len / 3); //一共有这么多个
                for (int i = 0; i < num; i++)
                {
                    if (info_len < 3)
                        break;

                    tmp16 = comFunc.ToUInt16(buf, index);
                    info_comb = "TEI[" + comFunc.BitField16(tmp16, 0, 12).ToString("X3")+"]";
                    tmp16 = comFunc.ToUInt16(buf, index + 1);
                    info_comb += "  接收率" + comFunc.BitField16(tmp16, 4, 8).ToString();
                    info_comb += "  信号强度" + rssi_info_get(0, comFunc.BitField16(tmp16, 12, 4));
                    rfdl_nb_chnl_info.信道信息.Add(info_comb);

                    info_len -= 3;
                    index += 3;
                }
            }

            return rfdl_nb_chnl_info;
        }


        public static rfdl_nb_chnl_bitinfo_c rfdl_nb_chnl_info_bitmap_deal(byte[] buf, int info_len, int start)
        {
            rfdl_nb_chnl_bitinfo_c chnl_info_bit = new rfdl_nb_chnl_bitinfo_c();
            byte type = comFunc.BitField8(buf[start], 0, 4);
            UInt16 tmp16;
            string info_comb = "";

            if (info_len < 1)
            {
                return chnl_info_bit;
            }

            if (type == 0)
            {
                chnl_info_bit.信道信息组合类型 = "接收率长度8bit | 平均信噪比0bit | 信号强度0bit";
            }
            else if (type == 1)
            {
                chnl_info_bit.信道信息组合类型 = "接收率长度8bit | 平均信噪比4bit | 信号强度0bit";
            }
            else if (type == 2)
            {
                chnl_info_bit.信道信息组合类型 = "接收率长度8bit | 平均信噪比0bit | 信号强度8bit";
            }
            else if (type == 3)
            {
                chnl_info_bit.信道信息组合类型 = "接收率长度8bit | 平均信噪比6bit | 信号强度6bit";
            }
            else if (type == 4)
            {
                chnl_info_bit.信道信息组合类型 = "接收率长度8bit | 平均信噪比4bit | 信号强度4bit";
            }
            else if (type == 5)
            {
                chnl_info_bit.信道信息组合类型 = "接收率长度8bit | 平均信噪比0bit | 信号强度4bit";
            }
            else
            {
                return chnl_info_bit;
            }
            chnl_info_bit.resv = comFunc.BitField8(buf[start], 4, 4);
            chnl_info_bit.信道信息 = new List<bitinfo_c>();
            int index = start;
            info_len--;
            index++;

            while (info_len >= 3)
            {
                byte snr;
                byte rssi;
                UInt16 start_tei = 0;
                UInt16 tmp_tei = 0;
                UInt16 bit_num = 0;
                UInt16 pos = 0;
                bitinfo_c bitinfo = new bitinfo_c();
                tmp16 = comFunc.ToUInt16(buf, index);
                start_tei = comFunc.BitField16(tmp16, 0, 12);
                bitinfo.位图起始TEI = start_tei.ToString("X3");
                bitinfo.resv = (byte)comFunc.BitField16(tmp16, 12, 4);
                bitinfo.位图大小 = buf[index + 2];
                if (info_len < (3 + bitinfo.位图大小))
                    break;

                bitinfo.位图 = new byte[bitinfo.位图大小];
                Array.Copy(buf, index + 3, bitinfo.位图, 0, bitinfo.位图大小);
                bitinfo.邻居节点信道信息 = new List<string>();
                info_len -= 3 + bitinfo.位图大小;
                index += 3 + bitinfo.位图大小;
                for (int i = 0; i < bitinfo.位图大小; i++)
                {
                    for (int j = 0; j < 8; j++)
                    {
                        if (comFunc.BitField8(bitinfo.位图[i], j, 1) == 1)
                        {
                            tmp_tei = (UInt16)(start_tei + i * 8 + j);
                            info_comb = "TEI[" + tmp_tei.ToString("X3") + "]";
                            switch (type)
                            {
                                case 0:
                                    if (info_len < (bit_num+1))
                                    {
                                        return chnl_info_bit;
                                    }
                                    info_comb += "  接收率" + buf[index+ bit_num];
                                    break;
                                case 1:
                                    if (info_len < ((bit_num+1)*12/8))
                                    {
                                        return chnl_info_bit;
                                    }
                                    pos = (UInt16)(bit_num * 12 / 8);
                                    tmp16 = comFunc.ToUInt16(buf, index+pos);
                                    if (bit_num * 12 % 8 == 4)
                                    {
                                        info_comb += "  接收率" + comFunc.BitField16(tmp16, 4, 8);
                                        info_comb += "  平均信噪比" + snr_info_get((byte)comFunc.BitField16(tmp16, 12, 4));
                                    }
                                    else
                                    {
                                        info_comb += "  接收率" + comFunc.BitField16(tmp16, 0, 8);
                                        info_comb += "  平均信噪比" + snr_info_get((byte)comFunc.BitField16(tmp16, 8, 4));
                                    }
                                    break;
                                case 2:
                                    if (info_len < ((bit_num+1) * 16 / 8))
                                    {
                                        return chnl_info_bit;
                                    }
                                    info_comb += "  接收率" + buf[index + bit_num*2];
                                    if (buf[index + bit_num * 2 + 1] == 0)
                                    {
                                        info_comb += "  信号强度RSSI<-110 dBm";
                                    }
                                    else
                                    {
                                        info_comb += "  信号强度RSSI=" + ((int)(buf[index + bit_num * 2 + 1]) - 111) + "dBm";
                                    }
                                    break;
                                case 3:
                                    if (info_len < ((bit_num + 1) * 20 / 8))
                                    {
                                        return chnl_info_bit;
                                    }
                                    pos = (UInt16)(bit_num * 20 / 8);
                                    if (bit_num * 12 % 8 == 4)
                                    {
                                        tmp16 = comFunc.ToUInt16(buf, index + pos);
                                        info_comb += "  接收率" + comFunc.BitField16(tmp16, 4, 8);
                                        tmp16 = comFunc.ToUInt16(buf, index + pos + 1);
                                        if (comFunc.BitField16(tmp16, 4, 6) == 0)
                                        {
                                            info_comb += "  平均信噪比SNR<-6 dB";
                                        }
                                        else
                                        {
                                            info_comb += "  平均信噪比SNR=" + ((int)comFunc.BitField16(tmp16, 4, 6) - 7) + "dB";
                                        }
                                        info_comb += "  信号强度" + rssi_info_get(1, (UInt16)comFunc.BitField16(tmp16, 10, 6));
                                    }
                                    else
                                    {
                                        info_comb += "  接收率" + buf[index + pos];
                                        tmp16 = comFunc.ToUInt16(buf, index + pos + 1);
                                        if (comFunc.BitField16(tmp16, 0, 6) == 0)
                                        {
                                            info_comb += "  平均信噪比SNR<-6 dB";
                                        }
                                        else
                                        {
                                            info_comb += "  平均信噪比SNR=" + ((int)comFunc.BitField16(tmp16, 0, 6) - 7) + "dB";
                                        }
                                        info_comb += "  信号强度" + rssi_info_get(1, (UInt16)comFunc.BitField16(tmp16, 6, 6));
                                    }
                                    break;
                                case 4:
                                    if (info_len < ((bit_num + 1) * 16 / 8))
                                    {
                                        return chnl_info_bit;
                                    }
                                    info_comb += "  接收率" + buf[index + bit_num * 2];
                                    snr = comFunc.BitField8(buf[index + bit_num * 2 + 1], 0, 4);
                                    rssi = comFunc.BitField8(buf[index + bit_num * 2 + 1], 4, 4);
                                    info_comb += "  平均信噪比" + snr_info_get((byte)snr);
                                    info_comb += "  信号强度" + rssi_info_get(0, rssi);
                                    break;
                                case 5:
                                    if (info_len < ((bit_num+1)*12/8))
                                    {
                                        return chnl_info_bit;
                                    }
                                    pos = (UInt16)(bit_num * 12 / 8);
                                    tmp16 = comFunc.ToUInt16(buf, index+pos);
                                    if (bit_num * 12 % 8 == 4)
                                    {
                                        info_comb += "  接收率" + comFunc.BitField16(tmp16, 4, 8);
                                        info_comb += "  信号强度" + rssi_info_get(0, (UInt16)comFunc.BitField16(tmp16, 12, 4));
                                    }
                                    else
                                    {
                                        info_comb += "  接收率" + comFunc.BitField16(tmp16, 0, 8);
                                        info_comb += "  信号强度" + comFunc.BitField16(tmp16, 8, 4);
                                    }
                                    break;
                            }
                            bit_num++;
                            bitinfo.邻居节点信道信息.Add(info_comb);
                        }
                    }
                }
                chnl_info_bit.信道信息.Add(bitinfo);
            }
            return chnl_info_bit;
        }



        public static aps_mng_c aps_mng_deal(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {
            
            UInt16 tmp16 = 0;
            aps_mng_c aps_mng = new aps_mng_c();

            if (simple_flag != 1)
            {
                if (buf.Length < (start + aps_mng.帧头原始数据.Length))
                {
                    aps_mng.具体帧类型 = 254;
                    return aps_mng;
                }
                Array.Copy(buf, start, aps_mng.帧头原始数据, 0, aps_mng.帧头原始数据.Length);
            }

            aps_mng.端口号 = buf[start];
            aps_mng.报文标识符 = comFunc.ToUInt16(buf, start + 1).ToString("X4");
            aps_mng.resv = buf[start + 3];

            tmp16 = comFunc.ToUInt16(buf, start + 4);
            aps_mng.帧类型 = (byte)comFunc.BitField16(tmp16, 0, 4);
            aps_mng.resv2 = (byte)comFunc.BitField16(tmp16, 4, 8);
            aps_mng.业务扩展域标识位 = (byte)comFunc.BitField16(tmp16, 12, 1);
            aps_mng.响应标识位 = (byte)comFunc.BitField16(tmp16, 13, 1);
            aps_mng.启动标志位 = (byte)comFunc.BitField16(tmp16, 14, 1);
            aps_mng.传输方向位 = (byte)comFunc.BitField16(tmp16, 15, 1);

            aps_mng.业务标识 = buf[start + 6];
            aps_mng.应用版本号 = buf[start + 7];
            aps_mng.帧序号 = comFunc.ToUInt16(buf, start + 8);
            aps_mng.帧长 = comFunc.ToUInt16(buf, start + 10);

            if (buf.Length < (start + 12 + aps_mng.帧长))
            {
                aps_mng.具体帧类型 = 254;
                return aps_mng;
            }

            if (simple_flag == 1)
            {
                if (aps_mng.传输方向位 == 0)
                {
                    detail += "↓";
                }
                else
                {
                    detail += "↑";
                }
                detail += "应用层SEQ:" + aps_mng.帧序号.ToString("X4");
            }
            else
            {
                aps_mng.业务原始数据 = new byte[aps_mng.帧长];
                Array.Copy(buf, start+12, aps_mng.业务原始数据, 0, aps_mng.业务原始数据.Length);
            }
                

            aps_mng.具体帧类型 = 255;
            if (aps_mng.端口号 == 0x11)
                aps_mng_port_11_deal(buf, bpsz, start+12, ref aps_mng, ref simple_flag, ref detail);
            else if (aps_mng.端口号 == 0x13)
                aps_mng_port_13_deal(buf, bpsz, start+12, ref aps_mng, ref simple_flag, ref detail);
            else
            { }

            return aps_mng;
        }

        /*国网应用层解析*/
        public static aps_mng_c_gw aps_mng_deal_gw(byte[] buf, int bpsz, int start, ref int simple_flag, ref string detail)
        {

            
            aps_mng_c_gw aps_mng = new aps_mng_c_gw();

            if (simple_flag != 1)
            {
                if (buf.Length < (start + aps_mng.帧头原始数据.Length))
                {
                    aps_mng.具体帧类型 = 254;
                    return aps_mng;
                }
                Array.Copy(buf, start, aps_mng.帧头原始数据, 0, aps_mng.帧头原始数据.Length);
            }

            aps_mng.报文端口号 = buf[start];

            aps_mng.报文ID = comFunc.ToUInt16(buf, start + 1);

            if (comFunc.BitField8(buf[start +2],4,4) == 0)
            {
                aps_mng.信道安全机制 = "0-明文传输";
            }
           
           

            aps_mng.具体帧类型 = 255;
            if (aps_mng.报文端口号 == 0x11)
                aps_mng_port_11_deal_gw(buf, bpsz, start + 4, ref aps_mng, ref simple_flag, ref detail);
            else if (aps_mng.报文端口号 == 0x12)
                aps_mng_port_12_deal_gw(buf, bpsz, start + 4, ref aps_mng, ref simple_flag, ref detail);
            else
            { }

            return aps_mng;
        }



        public static void aps_mng_port_11_deal(byte[] buf, int bpsz, int start, ref aps_mng_c aps_mng, ref int simple_flag, ref string detail)
        {
            if (aps_mng.帧类型 == 0)
            {//确认否认帧
                if (aps_mng.业务标识 == 0)
                {
                    aps_mng.具体帧类型 = 0;
                    aps_mng.帧类型含义 = "确认帧";
                }
                else if (aps_mng.业务标识 == 1)
                {
                    aps_mng.具体帧类型 = 1;
                    aps_mng.帧类型含义 = "否认帧";
                    aps_ack_no_c ack_no = new aps_ack_no_c();
                    if (buf[start] == 0)
                    {
                        ack_no.原因 = "通信超时";
                    }
                    else if (buf[start] == 1)
                    {
                        ack_no.原因 = "业务标识不支持";
                    }
                    else if (buf[start] == 2)
                    {
                        ack_no.原因 = "CCO忙";
                    }
                    else if (buf[start] == 3)
                    {
                        ack_no.原因 = "终端层无应答";
                    }
                    else if (buf[start] == 4)
                    {
                        ack_no.原因 = "格式错误";
                    }
                    else
                    {
                        ack_no.原因 = "其他" + buf[start];
                    }
                    if (simple_flag == 1)
                    {
                        detail += "|原因:" + ack_no.原因;
                    }
                    aps_mng.帧荷载解析 = (Object)ack_no;
                }
            }
            else if (aps_mng.帧类型 == 1)
            {//数据转发帧
                if (aps_mng.业务标识 == 0)
                {
                    if (bpsz < (start + 16))
                    {
                        aps_mng.具体帧类型 = 254;
                        return;
                    }
                    if (aps_mng.传输方向位 == 0) //下行
                    {
                        aps_mng.具体帧类型 = 2;
                        aps_mng.帧类型含义 = "设备数据传输下行";
                        aps_to_dev_down_c to_dev_down = new aps_to_dev_down_c ();
                        Array.Copy(buf, start, to_dev_down.源地址, 0, 6);
                        Array.Reverse(to_dev_down.源地址);
                        Array.Copy(buf, start+6, to_dev_down.目的地址, 0, 6);
                        Array.Reverse(to_dev_down.目的地址);
                        to_dev_down.设备超时时间s = buf[start + 12];
                        to_dev_down.resv = buf[start + 13];
                        to_dev_down.数据长度 = comFunc.ToUInt16(buf, start + 14);
                        if (bpsz < (start + 16 + to_dev_down.数据长度))
                        {
                            if (simple_flag == 1)
                            {
                                detail += "|数据长度有误";
                            }
                        }
                        else
                        {
                            if (simple_flag != 1)
                            {
                                to_dev_down.数据内容 = new byte[to_dev_down.数据长度];
                                Array.Copy(buf, start + 16, to_dev_down.数据内容, 0, to_dev_down.数据内容.Length);
                            }
                        }
                        aps_mng.帧荷载解析 = (Object)to_dev_down;

                    }
                    else
                    {
                        aps_mng.具体帧类型 = 2;
                        aps_mng.帧类型含义 = "设备数据传输上行";
                        aps_to_dev_up_c to_dev_up = new aps_to_dev_up_c();
                        Array.Copy(buf, start, to_dev_up.源地址, 0, 6);
                        Array.Reverse(to_dev_up.源地址);
                        Array.Copy(buf, start + 6, to_dev_up.目的地址, 0, 6);
                        Array.Reverse(to_dev_up.目的地址);
                        to_dev_up.resv = comFunc.ToUInt16(buf, start + 12);
                        to_dev_up.数据长度 = comFunc.ToUInt16(buf, start + 14);
                        if (bpsz < (start + 16 + to_dev_up.数据长度))
                        {
                            if (simple_flag == 1)
                            {
                                detail += "|数据长度有误";
                            }
                        }
                        else
                        {
                            if (simple_flag != 1)
                            {
                                to_dev_up.数据内容 = new byte[to_dev_up.数据长度];
                                Array.Copy(buf, start + 16, to_dev_up.数据内容, 0, to_dev_up.数据内容.Length);
                            }
                        }
                        
                        aps_mng.帧荷载解析 = (Object)to_dev_up;
                    }
                }

            }
            else if (aps_mng.帧类型 == 2)
            {//命令帧
                switch(aps_mng.业务标识)
                {
                    case 0: //查询终端搜索结果
                        if (aps_mng.传输方向位 == 0)
                        {
                            aps_mng.具体帧类型 = 4;
                            aps_mng.帧类型含义 = "查询终端搜索结果";
                        }
                        else
                        {
                            if (bpsz < (start + 4))
                            {
                                aps_mng.具体帧类型 = 254;
                                return;
                            }
                            aps_mng.具体帧类型 = 4;
                            aps_mng.帧类型含义 = "查询终端搜索结果应答";
                            aps_search_c aps_search = new aps_search_c();
                            aps_search.终端数量 = buf[start];
                            if (simple_flag != 1)
                            {
                                if (bpsz < (start + 4 + aps_search.终端数量 * 8))
                                {
                                    aps_mng.帧荷载解析 = (Object)aps_search;
                                    return;
                                }
                                Array.Copy(buf, start + 1, aps_search.resv, 0, 3);
                                if (aps_search.终端数量 != 0)
                                {
                                    aps_search.终端信息 = new List<term_info_c>();
                                    for (int i = 0; i < aps_search.终端数量; i++)
                                    {
                                        term_info_c term_info = new term_info_c();
                                        Array.Copy(buf, start + 4 + i * 8, term_info.终端地址, 0, 6);
                                        Array.Reverse(term_info.终端地址);
                                        if (buf[start + 4 + i * 8 + 6] == 1)
                                        {
                                            term_info.规约类型 = "DLT645-1997";
                                        }
                                        else if (buf[start + 4 + i * 8 + 6] == 2)
                                        {
                                            term_info.规约类型 = "DLT645-2007";
                                        }
                                        else if (buf[start + 4 + i * 8 + 6] == 2)
                                        {
                                            term_info.规约类型 = "CJT188";
                                        }
                                        else
                                        {
                                            term_info.规约类型 = buf[start + 4 + i * 8 + 6].ToString("X3");
                                        }

                                        term_info.resv = buf[start + 4 + i * 8 + 7];
                                        aps_search.终端信息.Add(term_info);
                                    }
                                }
                            }
                            else
                            {
                                detail += "|终端数量:" + aps_search.终端数量;
                                if (bpsz < (start + 4 + aps_search.终端数量 * 8))
                                {
                                    detail += "|终端数量有误";
                                }
                            }
                            aps_mng.帧荷载解析 = (Object)aps_search;
                        }
                        break;

                    case 1: //下发搜索终端列表
                        if (aps_mng.传输方向位 == 0)
                        {
                            if (bpsz < (start + 4))
                            {
                                aps_mng.具体帧类型 = 254;
                                return;
                            }
                            aps_mng.具体帧类型 = 5;
                            aps_mng.帧类型含义 = "下发搜索终端列表";
                            aps_search_c aps_Search = new aps_search_c();
                            aps_Search.终端数量 = buf[start];
                            if (simple_flag != 1)
                            {
                                if (bpsz < (start + 4 + aps_Search.终端数量 * 8))
                                {
                                    aps_mng.帧荷载解析 = (Object)aps_Search;
                                    return;
                                }
                                Array.Copy(buf, start + 1, aps_Search.resv, 0, 3);
                                if (aps_Search.终端数量 != 0)
                                {
                                    aps_Search.终端信息 = new List<term_info_c>();
                                    for (int i = 0; i < aps_Search.终端数量; i++)
                                    {
                                        term_info_c term_info = new term_info_c();
                                        Array.Copy(buf, start + 4 + i * 8, term_info.终端地址, 0, 6);
                                        Array.Reverse(term_info.终端地址);
                                        if (buf[start + 4 + i * 8 + 6] == 1)
                                        {
                                            term_info.规约类型 = "DLT645-1997";
                                        }
                                        else if (buf[start + 4 + i * 8 + 6] == 2)
                                        {
                                            term_info.规约类型 = "DLT645-2007";
                                        }
                                        else if (buf[start + 4 + i * 8 + 6] == 2)
                                        {
                                            term_info.规约类型 = "CJT188";
                                        }
                                        else
                                        {
                                            term_info.规约类型 = buf[start + 4 + i * 8 + 6].ToString("X3");
                                        }
                                        term_info.resv = buf[start + 4 + i * 8 + 7];
                                        aps_Search.终端信息.Add(term_info);
                                    }
                                }
                            }
                            else
                            {
                                detail += "|终端数量:" + aps_Search.终端数量;
                                if (bpsz < (start + 4 + aps_Search.终端数量 * 8))
                                {
                                    detail += "|终端数量有误";
                                }
                            }
                            aps_mng.帧荷载解析 = (Object)aps_Search;
                        }
                        break;
                    case 2: //文件传输
                        upgrade_deal(buf, bpsz, start, ref aps_mng, ref simple_flag,ref detail);
                        break;
                    case 3: //允许/禁止从节点事件
                        if (aps_mng.传输方向位 == 0)
                        {
                            if (bpsz < (start + 4))
                            {
                                aps_mng.具体帧类型 = 254;
                                return;
                            }
                            aps_mng.具体帧类型 = 10;
                            aps_mng.帧类型含义 = "允许and禁止从节点事件";
                            aps_slave_sta_evt_c slave_sta_evt = new aps_slave_sta_evt_c();
                            if (buf[start] == 0)
                            {
                                slave_sta_evt.从节点事件标识 = "禁止主动上报";
                            }
                            else if (buf[start] == 1)
                            {
                                slave_sta_evt.从节点事件标识 = "允许主动上报";
                            }
                            else
                            {
                                slave_sta_evt.从节点事件标识 =  buf[start].ToString();
                            }

                            Array.Copy(buf, start + 1, slave_sta_evt.resv, 0, 3);

                            if (simple_flag == 1)
                            {
                                detail += "|标识:" + slave_sta_evt.从节点事件标识;
                            }
                            aps_mng.帧荷载解析 = (Object)slave_sta_evt;
                        }
                        break;

                    case 4: //从节点重启
                        if (aps_mng.传输方向位 == 0)
                        {
                            if (bpsz < (start + 4))
                            {
                                aps_mng.具体帧类型 = 254;
                                return;
                            }
                            aps_mng.具体帧类型 = 11;
                            aps_mng.帧类型含义 = "从节点重启";
                            aps_sta_reboot_c sta_reboot = new aps_sta_reboot_c();
                            sta_reboot.延时重启时间s = buf[start];
                            Array.Copy(buf, start + 1, sta_reboot.resv, 0, 3);
                            detail += "|延时时间s:" + sta_reboot.延时重启时间s;
                            aps_mng.帧荷载解析 = (Object)sta_reboot;
                        }
                        break;

                    case 5: //从节点信息查询
                        aps_mng.具体帧类型 = 12;
                        if (aps_mng.传输方向位 == 0)
                        {
                            if (bpsz < (start + 1))
                            {
                                aps_mng.具体帧类型 = 254;
                                return;
                            }
                            aps_mng.帧类型含义 = "从节点信息查询";
                            aps_sta_info_query_down_c sta_info_query_down = new aps_sta_info_query_down_c();
                            sta_info_query_down.信息列表元素数量 = buf[start];
                            sta_info_query_down.信息元素ID = new byte[sta_info_query_down.信息列表元素数量];
                            Array.Copy(buf, start + 1, sta_info_query_down.信息元素ID, 0, sta_info_query_down.信息列表元素数量);
                            detail += "|查询数量:" + sta_info_query_down.信息列表元素数量;
                            if (bpsz < (start + 1 + sta_info_query_down.信息列表元素数量))
                            {
                                detail += "|查询数量有误";
                            }
                            aps_mng.帧荷载解析 = (Object)sta_info_query_down;
                        }
                        else
                        {
                            aps_mng.帧类型含义 = "从节点信息查询应答";
                            aps_sta_info_query_up_c sta_info_query_up = new aps_sta_info_query_up_c();
                            sta_info_query_up.信息列表元素数量 = buf[start];
                            if (simple_flag != 1)
                            {
                                if (sta_info_query_up.信息列表元素数量 != 0)
                                {
                                    sta_info_query_up.信息元素信息 = new List<info_query_up_c>();
                                    start += 1;
                                    for (int i = 0; i < sta_info_query_up.信息列表元素数量; i++)
                                    {
                                        info_query_up_c info = new info_query_up_c();
                                        switch(buf[start])
                                        {
                                            case 0x00:
                                                info.元素ID = "厂商代码";
                                                break;
                                            case 0x01:
                                                info.元素ID = "模块软件版本";
                                                break;
                                            case 0x02:
                                                info.元素ID = "BOOT版本";
                                                break;
                                            case 0x03:
                                                info.元素ID = "升级文件CRC32";
                                                break;
                                            case 0x04:
                                                info.元素ID = "升级文件长度";
                                                break;
                                            case 0x05:
                                                info.元素ID = "芯片厂商代码";
                                                break;
                                            case 0x06:
                                                info.元素ID = "模块软件日期";
                                                break;
                                            case 0x07:
                                                info.元素ID = "文件传输扩展状态字";
                                                break;
                                            case 0x08:
                                                info.元素ID = "模块出厂MAC地址";
                                                break;
                                            case 0x09:
                                                info.元素ID = "模块硬件版本信息";
                                                break;
                                            case 0x0A:
                                                info.元素ID = "模块硬件发布日期";
                                                break;
                                            case 0x0B:
                                                info.元素ID = "芯片软件版本号";
                                                break;
                                            case 0x0C:
                                                info.元素ID = "芯片软件发布日期";
                                                break;
                                            case 0x0D:
                                                info.元素ID = "芯片硬件版本号";
                                                break;
                                            case 0x0E:
                                                info.元素ID = "芯片硬件发布日期";
                                                break;
                                            case 0x0F:
                                                info.元素ID = "应用程序版本号";
                                                break;
                                            case 0x10:
                                                info.元素ID = "通信模块资产编码";
                                                break;
                                            default:
                                                info.元素ID = "保留[" + buf[start].ToString("X2") + "]";
                                                break;
                                        }
                                        info.元素数据长度 = buf[start + 1];
                                        if (info.元素数据长度 > (buf.Length - (start + 2)))
                                        {
                                            aps_mng.解析结果 = "info.元素数据长度:" + info.元素数据长度 + "剩下" + (buf.Length - (start + 2));
                                            break;
                                        }
                                        info.元素数据 = new byte[info.元素数据长度];
                                        Array.Copy(buf, start + 2, info.元素数据, 0, info.元素数据长度);
                                        sta_info_query_up.信息元素信息.Add(info);
                                        start += 2 + info.元素数据长度;
                                    }
                                }
                            }
                            else
                            {
                                detail += "|应答数量:" + sta_info_query_up.信息列表元素数量;
                            }

                            aps_mng.帧荷载解析 = (Object)sta_info_query_up;

                        }
                        break;
                    case 6: //下发通信地址映射表列表
                        if (aps_mng.传输方向位 == 0)
                        {
                            if (bpsz < (start + 4))
                            {
                                aps_mng.具体帧类型 = 254;
                                return;
                            }
                            aps_mng.具体帧类型 = 13;
                            aps_mng.帧类型含义 = "下发通信地址映射表列表";
                            aps_term_map_down_c term_map_down = new aps_term_map_down_c();
                            term_map_down.映射终端数量 = buf[start];
                            if (simple_flag != 1)
                            {
                                if (bpsz >= (start + 4 + term_map_down.映射终端数量 * 18))
                                {
                                    Array.Copy(buf, start + 1, term_map_down.resv, 0, 3);
                                    if (term_map_down.映射终端数量 != 0)
                                    {
                                        term_map_down.映射终端信息 = new List<term_map_c>();
                                        start += 4;
                                        for (int i = 0; i < term_map_down.映射终端数量; i++)
                                        {
                                            term_map_c term_map = new term_map_c();
                                            Array.Copy(buf, start + i * 18, term_map.通信地址, 0, 6);
                                            Array.Reverse(term_map.通信地址);
                                            Array.Copy(buf, start + i * 18 + 6, term_map.终端地址, 0, 12);
                                            Array.Reverse(term_map.终端地址);
                                        }
                                    }
                                }

                            }
                            else
                            {
                                detail += "|下发映射终端数量:" + term_map_down.映射终端信息;
                                if (bpsz < (start + 4 + term_map_down.映射终端数量 * 18))
                                {
                                    detail += "|下发映射终端数量有误";
                                }
                            }
                            aps_mng.帧荷载解析 = (Object)term_map_down;
                        }
                        break;
                    case 0x10: //台区户变关系/相位识别
                        if (bpsz < (start + 12))
                        {
                            aps_mng.具体帧类型 = 254;
                            return;
                        }
                        int phase = 0;
                        aps_ad_phase_c aps_ad_phase = new aps_ad_phase_c();
                        aps_ad_phase.报文头长度 = comFunc.BitField8(buf[start], 0, 6);
                        phase = comFunc.BitField8(buf[start], 6, 2);
                        if (phase == 0)
                        {
                            aps_ad_phase.采集相位 = "默认相位";
                        }
                        else if (phase == 1)
                        {
                            aps_ad_phase.采集相位 = "第一出线相位";
                        }
                        else if (phase == 2)
                        {
                            aps_ad_phase.采集相位 = "第二出线相位";
                        }
                        else if (phase == 3)
                        {
                            aps_ad_phase.采集相位 = "第三出线相位";
                        }
                        aps_ad_phase.resv = buf[start + 1];
                        aps_ad_phase.resv2 = comFunc.ToUInt16(buf, start + 2);
                        Array.Copy(buf, start + 4, aps_ad_phase.MAC地址, 0, 6);
                        if (buf[start + 10] == 1)
                        {
                            aps_ad_phase.特征类型 = "工频电压特征";
                        }
                        else if (buf[start + 10] == 2)
                        {
                            aps_ad_phase.特征类型 = "工频频率特征";
                        }
                        else if (buf[start + 10] == 3)
                        {
                            aps_ad_phase.特征类型 = "工频周期特征";
                        }
                        else
                        {
                            aps_ad_phase.特征类型 = "保留：" + buf[start + 10];
                        }
                        if (buf[start + 11] == 1)
                        {
                            aps_ad_phase.采集类型 = "台区特征采集启动";
                        }
                        else if (buf[start + 11] == 2)
                        {
                            aps_ad_phase.采集类型 = "台区特征信息收集";
                        }
                        else if (buf[start + 11] == 3)
                        {
                            aps_ad_phase.采集类型 = "台区特征信息告知";
                        }
                        else if (buf[start + 11] == 4)
                        {
                            aps_ad_phase.采集类型 = "台区判别结果查询";
                        }
                        else if (buf[start + 11] == 5)
                        {
                            aps_ad_phase.采集类型 = "台区判别结果信息";
                        }
                        else if (buf[start + 11] == 6)
                        {
                            aps_ad_phase.采集类型 = "相位特征采集指示";
                        }
                        else if (buf[start + 11] == 7)
                        {
                            aps_ad_phase.采集类型 = "相位特征采集告知";
                        }
                        else
                        {
                            aps_ad_phase.采集类型 = "保留[" + buf[start + 11] + "]";
                        }
                        
                        if (buf[start + 11] == 6 || buf[start + 11] == 7)
                        {
                            aps_mng.具体帧类型 = 17;
                        }
                        else
                        {
                            aps_mng.具体帧类型 = 16;
                        }
                        if (simple_flag == 1)
                        {
                            detail += "|" + aps_ad_phase.采集类型;
                            if (buf[start + 11] == 5)
                            {
                                detail += buf[start + 14] == 1 ? "|识别结束" : "|识别未结束";
                                if (buf[start + 15] == 1)
                                {
                                    detail += "|本台区";
                                }
                                else if (buf[start + 15] == 2)
                                {
                                    detail += "|非本台区";
                                }
                                else
                                {
                                    detail += "|结果未知";
                                }
                                byte[] cco = new byte[6];
                                Array.Copy(buf, start + 16, cco, 0, 6);
                                detail += "|CCO[" + comFunc.ByteArryToHexStrWithoutBlock(cco) + "]";
                            }
                        }
                        else
                        {
                            aps_ad_phase.数据 = new byte[aps_mng.帧长 - 12];
                            Array.Copy(buf, start + 12, aps_ad_phase.数据, 0, aps_mng.帧长 - 12);
                            mtad_deal(aps_ad_phase.数据, buf[start + 10], buf[start + 11], ref aps_ad_phase.数据具体解析);
                        }
                        aps_mng.帧类型含义 = "台区户变关系and相位识别";
                        
                        aps_mng.帧荷载解析 = (Object)aps_ad_phase;
                        break;
                    case 0xF0: //测试帧
                        if (aps_mng.传输方向位 == 0)
                        {
                            byte tmp = 0;
                            aps_mng.具体帧类型 = 18;
                            aps_mng.帧类型含义 = "测试帧";
                            aps_test_c aps_test = new aps_test_c();
                            if (buf[start] == 0)
                            {
                                aps_test.测试ID = "进入回环测试模式";
                            }
                            else if (buf[start] == 1)
                            {
                                aps_test.测试ID = "进入透明转发模式";
                            }
                            else if (buf[start] == 1)
                            {
                                aps_test.测试ID = "频段切换命令";
                            }
                            else
                            {
                                aps_test.测试ID = buf[start].ToString("X2");
                            }

                            if (simple_flag != 1)
                            {
                                aps_test.resv = buf[start + 1];
                                aps_test.数据长度 = comFunc.ToUInt16(buf, start + 2);
                                aps_test.数据 = new byte[aps_test.数据长度];
                                Array.Copy(buf, start + 4, aps_test.数据, 0, aps_test.数据长度);
                            }
                            else
                            {
                                detail += "|ID:" + aps_test.测试ID;
                                tmp = comFunc.BitField8(buf[start + 4], 0, 4);
                                detail += "|频段:" + tmp;
                            }
                            aps_mng.帧荷载解析 = (Object)aps_test;
                        }
                        break;

                }

            }
            else if (aps_mng.帧类型 == 3)
            {//主动上报帧
                if (aps_mng.业务标识 == 0) //电表事件主动上报
                {
                    aps_mng.具体帧类型 = 19;
                    aps_mng.帧类型含义 = "电表事件主动上报";
                    aps_evt_rpt_up_c aps_evt_rpt_up = new aps_evt_rpt_up_c();
                    Array.Copy(buf, start, aps_evt_rpt_up.电表地址, 0, 6);
                    Array.Reverse(aps_evt_rpt_up.电表地址);
                    if (simple_flag != 1)
                    {
                        aps_evt_rpt_up.电表主动上报报文 = new byte[aps_mng.帧长 - 6];
                        Array.Copy(buf, start + 6, aps_evt_rpt_up.电表主动上报报文, 0, aps_mng.帧长 - 6);
                    }
                    aps_mng.帧荷载解析 = (Object)aps_evt_rpt_up;
                }
                else if(aps_mng.业务标识 == 2)//设备事件主动上报
                {
                    aps_mng.具体帧类型 = 20;
                    aps_mng.帧类型含义 = "设备事件主动上报";
                    aps_dev_evt_rpt_up_c aps_dev_evt_rpt_up = new aps_dev_evt_rpt_up_c();
                    Array.Copy(buf, start, aps_dev_evt_rpt_up.设备地址, 0, 6);
                    Array.Reverse(aps_dev_evt_rpt_up.设备地址);
                    if (simple_flag != 1)
                    {
                        aps_dev_evt_rpt_up.resv = comFunc.ToUInt16(buf, start + 6);
                        aps_dev_evt_rpt_up.设备主动上报报文 = new byte[aps_mng.帧长 - 8];
                        Array.Copy(buf, start + 8, aps_dev_evt_rpt_up.设备主动上报报文, 0, aps_mng.帧长 - 8);
                    }
                    aps_mng.帧荷载解析 = (Object)aps_dev_evt_rpt_up;
                }

            }
            else if (aps_mng.帧类型 == 4)
            {//抄控器相关协议
                if (aps_mng.业务标识 == 0)
                {
                    aps_mng.具体帧类型 = 23;
                    aps_mng.帧类型含义 = "CKQ-CCO";
                    apc_ckq_cco_c apc_ckq_cco = new apc_ckq_cco_c();
                    if (simple_flag != 1)
                    {
                        apc_ckq_cco.协议类型 = buf[start];
                        apc_ckq_cco.序号 = buf[start + 1];
                        apc_ckq_cco.报文长度 = comFunc.ToUInt16(buf, start + 2);
                        apc_ckq_cco.报文内容 = new byte[apc_ckq_cco.报文长度];
                        Array.Copy(buf, start + 4, apc_ckq_cco.报文内容, 0, apc_ckq_cco.报文长度);
                    }
                    else
                    {
                        detail += "|序号:" + buf[start + 1];
                    }
                    aps_mng.帧荷载解析 = (Object)apc_ckq_cco;


                }
                else if (aps_mng.业务标识 == 1)
                {
                    aps_mng.具体帧类型 = 24;
                    aps_mng.帧类型含义 = "CKQ-串口";
                    aps_ckq_serial_c aps_ckq_serial = new aps_ckq_serial_c();
                    if (simple_flag != 1)
                    {
                        aps_ckq_serial.协议类型 = buf[start];
                        aps_ckq_serial.启动标识 = comFunc.BitField8(buf[start + 1], 0, 1) == 0 ? "回复报文，模块收到后不需要转发到串口" : "主动报文，模块收到后\r\n需要把内容转发到串口";
                        aps_ckq_serial.resv = comFunc.BitField8(buf[start + 1], 1, 7);
                        aps_ckq_serial.串口波特率 = comFunc.ToUInt32(buf, 2);
                        aps_ckq_serial.序号 = buf[start + 6];
                        Array.Copy(buf, start + 7, aps_ckq_serial.resv2, 0, 3);
                        aps_ckq_serial.报文长度 = comFunc.ToUInt16(buf, 10);
                        aps_ckq_serial.报文内容 = new byte[aps_ckq_serial.报文长度];
                        Array.Copy(buf, start + 12, aps_ckq_serial.报文内容, 0, aps_ckq_serial.报文长度);
                    }
                    else
                    {
                        detail += "|序号:" + buf[start + 6];
                    }
                    aps_mng.帧荷载解析 = (Object)aps_ckq_serial;

                }
            }
            else if (aps_mng.帧类型 == 5)
            {//广播命令帧
                aps_mng.具体帧类型 = 25;
                aps_mng.帧类型含义 = "广播帧";
                aps_brd_c aps_brd = new aps_brd_c();
                Array.Copy(buf, start, aps_brd.源地址, 0, 6);
                Array.Copy(buf, start+6, aps_brd.目的地址, 0, 6);
                if (aps_mng.业务标识 == 4)
                {
                    detail += "|从节点重启";
                }
                else if(aps_mng.业务标识 == 5)
                {
                    detail += "|从节点信息查询"; 
                }
                aps_mng.帧荷载解析 = (Object)aps_brd;
            }
            else if (aps_mng.帧类型 == 6)
            {//数据订阅路由帧
                if (aps_mng.业务标识 == 0)
                {
                    aps_mng.具体帧类型 = 26;
                    if (aps_mng.传输方向位 == 0)
                    {
                        aps_mng.帧类型含义 = "数据订阅路由下行";
                        aps_data_subscribe_down_c d_subscribe_down = new aps_data_subscribe_down_c();
                        Array.Copy(buf, start, d_subscribe_down.源地址, 0, 6);
                        Array.Reverse(d_subscribe_down.源地址);
                        Array.Copy(buf, start + 6, d_subscribe_down.目的地址, 0, 6);
                        Array.Reverse(d_subscribe_down.目的地址);
                        if (simple_flag != 1)
                        {
                            d_subscribe_down.设备超时时间s = buf[start + 12];
                            d_subscribe_down.resv = buf[start + 13];
                            d_subscribe_down.报文长度 = comFunc.ToUInt16(buf, start + 14);
                            d_subscribe_down.报文内容 = new byte[d_subscribe_down.报文长度];
                            Array.Copy(buf, start + 16, d_subscribe_down.报文内容, 0, d_subscribe_down.报文长度);
                        }
                        aps_mng.帧荷载解析 = (Object)d_subscribe_down;
                    }
                    else
                    {
                        aps_mng.帧类型含义 = "数据订阅路由上行";
                        aps_data_subscribe_up_c d_subscribe_up = new aps_data_subscribe_up_c();
                        Array.Copy(buf, start, d_subscribe_up.源地址, 0, 6);
                        Array.Reverse(d_subscribe_up.源地址);
                        Array.Copy(buf, start + 6, d_subscribe_up.目的地址, 0, 6);
                        Array.Reverse(d_subscribe_up.目的地址);
                        if (simple_flag != 1)
                        {
                            d_subscribe_up.resv = comFunc.ToUInt16(buf, start + 12);
                            d_subscribe_up.报文长度 = comFunc.ToUInt16(buf, start + 14);
                            d_subscribe_up.报文内容 = new byte[d_subscribe_up.报文长度];
                            Array.Copy(buf, start + 16, d_subscribe_up.报文内容, 0, d_subscribe_up.报文长度);
                        }
                        aps_mng.帧荷载解析 = (Object)d_subscribe_up;
                    }
                    
                }
            }
        }


        public static void aps_mng_port_11_deal_gw(byte[] buf, int bpsz, int start, ref aps_mng_c_gw aps_mng, ref int simple_flag, ref string detail)
        {
            UInt16 tmp16 = 0;
            UInt32 tmp24 = 0;
            if (aps_mng.报文ID == 1)
            {//确认否认帧
                
                aps_mng.具体帧类型 = 1;
                aps_mng.帧类型含义 = "终端主动抄表";
                aps_to_dev_down_up_gw down_up = new aps_to_dev_down_up_gw();
                tmp16 = comFunc.ToUInt16(buf, start );
                
                if (comFunc.BitField8(buf[start + 7], 0, 1) == 0)//下行
                {
                    down_up.传输方向 = "下行";
                    down_up.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                    down_up.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);
                    down_up.配置字 = (byte)comFunc.BitField16(tmp16, 12, 4);
                    if (comFunc.BitField16(buf[start+2], 0, 4) == 0)
                    {
                        down_up.转发数据的规约类型 = "透明传输";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 1)
                    {
                        down_up.转发数据的规约类型 = "DL/T645-1997";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 2)
                    {
                        down_up.转发数据的规约类型 = "DL/T645-2007";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 3)
                    {
                        down_up.转发数据的规约类型 = "DL/T698-45";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 5)
                    {
                        down_up.转发数据的规约类型 = "越南IEC103协议";
                    }
                    else 
                    {
                        down_up.转发数据的规约类型 = "保留";
                    }

                    down_up.转发数据长度 = comFunc.BitField16(comFunc.ToUInt16(buf, start+2), 4, 12);

                    down_up.报文序号 = comFunc.ToUInt16(buf, start+4);

                    down_up.设备超时时间ms = buf[start+6] * 100;
                    down_up.选项字 = comFunc.BitField8(buf[start + 7],0,8).ToString("X2");
                   


                }
                else//上行
                {
                    down_up.传输方向 = "上行";
                    down_up.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                    down_up.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);
                    down_up.应答状态 = (byte)comFunc.BitField16(tmp16, 12, 4);
                    if (comFunc.BitField16(buf[start + 2], 0, 4) == 0)
                    {
                        down_up.转发数据的规约类型 = "透明传输";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 1)
                    {
                        down_up.转发数据的规约类型 = "DL/T645-1997";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 2)
                    {
                        down_up.转发数据的规约类型 = "DL/T645-2007";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 3)
                    {
                        down_up.转发数据的规约类型 = "DL/T698-45";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 5)
                    {
                        down_up.转发数据的规约类型 = "越南IEC103协议";
                    }
                    else
                    {
                        down_up.转发数据的规约类型 = "保留";
                    }

                    down_up.转发数据长度 = comFunc.BitField16(comFunc.ToUInt16(buf, start+2), 4, 12);

                    down_up.报文序号 = comFunc.ToUInt16(buf, start + 4);

                    down_up.选项字 = comFunc.ToUInt16(buf, start + 6).ToString("X2");


                }

                if (simple_flag == 1)
                {
                    detail += down_up.传输方向;
                    detail += "|APS_SEQ:" + down_up.报文序号.ToString("X4");
                    detail += "|" + down_up.转发数据的规约类型;
                }
               
                aps_mng.帧荷载解析 = (Object)down_up;
                
            }

            else if (aps_mng.报文ID == 2)
            {//确认否认帧

                aps_mng.具体帧类型 = 2;
                aps_mng.帧类型含义 = "路由主动抄表";
                aps_to_dev_down_up_gw down_up = new aps_to_dev_down_up_gw();
                tmp16 = comFunc.ToUInt16(buf, start);

                if (comFunc.BitField8(buf[start + 7], 0, 1) == 0)//下行
                {
                    down_up.传输方向 = "下行";
                    down_up.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                    down_up.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);
                    down_up.配置字 = (byte)comFunc.BitField16(tmp16, 12, 4);
                    if (comFunc.BitField16(buf[start + 2], 0, 4) == 0)
                    {
                        down_up.转发数据的规约类型 = "透明传输";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 1)
                    {
                        down_up.转发数据的规约类型 = "DL/T645-1997";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 2)
                    {
                        down_up.转发数据的规约类型 = "DL/T645-2007";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 3)
                    {
                        down_up.转发数据的规约类型 = "DL/T698-45";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 5)
                    {
                        down_up.转发数据的规约类型 = "越南IEC103协议";
                    }
                    else
                    {
                        down_up.转发数据的规约类型 = "保留";
                    }

                    down_up.转发数据长度 = comFunc.BitField16(comFunc.ToUInt16(buf, start + 2), 4, 12);

                    down_up.报文序号 = comFunc.ToUInt16(buf, start + 4);

                    down_up.设备超时时间ms = buf[start + 6] * 100;
                    down_up.选项字 = comFunc.BitField8(buf[start + 7], 0, 8).ToString("X2");



                }
                else//上行
                {
                    down_up.传输方向 = "上行";
                    down_up.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                    down_up.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);
                    down_up.应答状态 = (byte)comFunc.BitField16(tmp16, 12, 4);
                    if (comFunc.BitField16(buf[start + 2], 0, 4) == 0)
                    {
                        down_up.转发数据的规约类型 = "透明传输";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 1)
                    {
                        down_up.转发数据的规约类型 = "DL/T645-1997";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 2)
                    {
                        down_up.转发数据的规约类型 = "DL/T645-2007";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 3)
                    {
                        down_up.转发数据的规约类型 = "DL/T698-45";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 5)
                    {
                        down_up.转发数据的规约类型 = "越南IEC103协议";
                    }
                    else
                    {
                        down_up.转发数据的规约类型 = "保留";
                    }

                    down_up.转发数据长度 = comFunc.BitField16(comFunc.ToUInt16(buf, start + 2), 4, 12);

                    down_up.报文序号 = comFunc.ToUInt16(buf, start + 4);

                    down_up.选项字 = comFunc.ToUInt16(buf, start + 6).ToString("X2");


                }

                if (simple_flag == 1)
                {
                    detail += down_up.传输方向;
                    detail += "|APS_SEQ:" + down_up.报文序号.ToString("X4");
                    detail += "|" + down_up.转发数据的规约类型;
                }

                aps_mng.帧荷载解析 = (Object)down_up;

            }

            else if (aps_mng.报文ID == 3)
            {//确认否认帧

                aps_mng.具体帧类型 = 3;
                aps_mng.帧类型含义 = "终端主动并发抄表";
                aps_to_dev_down_up_gw down_up = new aps_to_dev_down_up_gw();
                tmp16 = comFunc.ToUInt16(buf, start);

                if (comFunc.BitField8(buf[start + 7], 0, 1) == 0)//下行
                {
                    down_up.传输方向 = "下行";
                    down_up.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                    down_up.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);
                    down_up.配置字 = (byte)comFunc.BitField16(tmp16, 12, 4);

                    if(comFunc.BitField16(tmp16, 12, 1) == 0)
                    {
                        down_up.未应答重试标志 = "不重试";
                    }
                    else
                    {
                        down_up.未应答重试标志 = "重试";
                    }

                    if (comFunc.BitField16(tmp16, 13, 1) == 0)
                    {
                        down_up.否认重试标志 = "不重试";
                    }
                    else
                    {
                        down_up.否认重试标志 = "重试";
                    }

                    down_up.最大重试次数 = comFunc.BitField16(tmp16, 14, 2);

                    if (comFunc.BitField16(buf[start + 2], 0, 4) == 0)
                    {
                        down_up.转发数据的规约类型 = "透明传输";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 1)
                    {
                        down_up.转发数据的规约类型 = "DL/T645-1997";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 2)
                    {
                        down_up.转发数据的规约类型 = "DL/T645-2007";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 3)
                    {
                        down_up.转发数据的规约类型 = "DL/T698-45";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 5)
                    {
                        down_up.转发数据的规约类型 = "越南IEC103协议";
                    }
                    else
                    {
                        down_up.转发数据的规约类型 = "保留";
                    }

                    down_up.转发数据长度 = comFunc.BitField16(comFunc.ToUInt16(buf, start + 2), 4, 12);

                    down_up.报文序号 = comFunc.ToUInt16(buf, start + 4);

                    down_up.设备超时时间ms = buf[start + 6] * 100;
                    down_up.选项字 = comFunc.BitField8(buf[start + 7], 0, 8).ToString("X2");
                    down_up.报文间隔ms = comFunc.BitField8(buf[start + 7], 0, 8) * 10;


                }
                else//上行
                {
                    down_up.传输方向 = "上行";
                    down_up.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                    down_up.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);
                    down_up.应答状态 = (byte)comFunc.BitField16(tmp16, 12, 4);
                    if (comFunc.BitField16(buf[start + 2], 0, 4) == 0)
                    {
                        down_up.转发数据的规约类型 = "透明传输";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 1)
                    {
                        down_up.转发数据的规约类型 = "DL/T645-1997";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 2)
                    {
                        down_up.转发数据的规约类型 = "DL/T645-2007";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 3)
                    {
                        down_up.转发数据的规约类型 = "DL/T698-45";
                    }
                    else if (comFunc.BitField16(buf[start + 2], 0, 4) == 5)
                    {
                        down_up.转发数据的规约类型 = "越南IEC103协议";
                    }
                    else
                    {
                        down_up.转发数据的规约类型 = "保留";
                    }

                    down_up.转发数据长度 = comFunc.BitField16(comFunc.ToUInt16(buf, start + 2), 4, 12);

                    down_up.报文序号 = comFunc.ToUInt16(buf, start + 4);

                    down_up.选项字 = comFunc.ToUInt16(buf, start + 6).ToString("X2");


                }

                if (simple_flag == 1)
                {
                    detail += "|APS_SEQ:" + down_up.报文序号.ToString("X4");
                    detail += "|" + down_up.转发数据的规约类型;
                }

                aps_mng.帧荷载解析 = (Object)down_up;

            }

            else if (aps_mng.报文ID == 4)
            {
                aps_mng.具体帧类型 = 4;
                aps_mng.帧类型含义 = "校时";
                brd_time_sync_gw brd_time = new brd_time_sync_gw();
                tmp16 = comFunc.ToUInt16(buf, start);
                brd_time.传输方向 = "下行";

                brd_time.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                brd_time.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);

                tmp16 = comFunc.ToUInt16(buf, start + 2);
                brd_time.数据长度 = comFunc.BitField16(tmp16, 4, 12);

                aps_mng.帧荷载解析 = (Object)brd_time;
            }

            else if (aps_mng.报文ID == 6)
            {
                aps_mng.具体帧类型 = 5;
                aps_mng.帧类型含义 = "通讯测试";
                comm_test_gw time = new comm_test_gw();
                tmp16 = comFunc.ToUInt16(buf, start);
                time.传输方向 = "下行";

                time.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                time.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);

                tmp16 = comFunc.ToUInt16(buf, start + 2);
                if (comFunc.BitField16(tmp16, 0, 4) == 0)
                {
                    time.转发数据的规约类型 = "透明传输";
                }
                else if (comFunc.BitField16(tmp16, 0, 4) == 1)
                {
                    time.转发数据的规约类型 = "DL/T645-1997";
                }
                else if (comFunc.BitField16(tmp16, 0, 4) == 2)
                {
                    time.转发数据的规约类型 = "DL/T645-2007";
                }
                else if (comFunc.BitField16(tmp16, 0, 4) == 3)
                {
                    time.转发数据的规约类型 = "DL/T698-45";
                }
                else
                {
                    time.转发数据的规约类型 = "保留";
                }
                time.转发数据长度 = comFunc.BitField16(tmp16, 4, 12);

                aps_mng.帧荷载解析 = (Object)time;
            }

            else if (aps_mng.报文ID == 8)
            {
                aps_mng.具体帧类型 = 6;
                aps_mng.帧类型含义 = "事件上报";
                event_report_gw event_report = new event_report_gw();
                tmp16 = comFunc.ToUInt16(buf, start);
                tmp24 = comFunc.ToUInt24(buf, start + 1);

                event_report.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                event_report.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);
                if (comFunc.BitField16(tmp16, 12, 1) == 0)
                {
                    event_report.传输方向 = "下行";
                }
                else
                {
                    event_report.传输方向 = "上行";
                }

                if (comFunc.BitField16(tmp16, 13, 1) == 0)
                {
                    event_report.启动位 = "来自从动站";
                }
                else
                {
                    event_report.启动位 = "来自启动站";
                }


                if (comFunc.BitField32(tmp24, 6, 6) == 1 && event_report.传输方向 == "下行")
                {
                    event_report.功能码 = "CCO应答确认给STA";
                }
                else if (comFunc.BitField32(tmp24, 6, 6) == 2 && event_report.传输方向 == "下行")
                {
                    event_report.功能码 = "CCO下发允许事假主动上报给STA";
                }
                else if (comFunc.BitField32(tmp24, 6, 6) == 3 && event_report.传输方向 == "下行")
                {
                    event_report.功能码 = "CCO下发禁止事假主动上报给STA";
                }
                else if (comFunc.BitField32(tmp24, 6, 6) == 4 && event_report.传输方向 == "下行")
                {
                    event_report.功能码 = "CCO应答事件缓存区满给STA";
                }
                else if (comFunc.BitField32(tmp24, 6, 6) == 1 && event_report.传输方向 == "上行")
                {
                    event_report.功能码 = "STA主动上报事件给CCO(电表触发)";
                }
                else if (comFunc.BitField32(tmp24, 6, 6) == 2 && event_report.传输方向 == "上行")
                {
                    event_report.功能码 = "STA主动上报事件给CCO(模块触发)";
                }
                else if (comFunc.BitField32(tmp24, 6, 6) == 3 && event_report.传输方向 == "上行")
                {
                    event_report.功能码 = "STA主动上报事件给CCO(采集器触发)";
                }

                event_report.转发数据长度 = comFunc.BitField32(tmp24, 12, 12);
                event_report.报文序号 = comFunc.ToUInt16(buf, start + 4);
                Array.Copy(buf, start + 6, event_report.电能表地址, 0, 6);

                aps_mng.帧荷载解析 = (Object)event_report;
            }

            else if (aps_mng.报文ID == 17)
            {
                aps_mng.具体帧类型 = 7;
                aps_mng.帧类型含义 = "查询从节点主动注册";
                slave_node_active_gw slave_node = new slave_node_active_gw();
                tmp16 = comFunc.ToUInt16(buf, start);
                slave_node.传输方向 = "下行";

                slave_node.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                slave_node.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);
                if ((byte)comFunc.BitField16(tmp16, 12, 1) == 0)
                {
                    slave_node.强制应答标志 = "非强制应答";
                    slave_node.从节点参数注册 = (byte)comFunc.BitField16(tmp16, 13, 3);
                    slave_node.报文序号 = comFunc.ToUInt32(buf, start + 4);
                    Array.Copy(buf, start + 8, slave_node.源MAC地址, 0, 6);
                    Array.Copy(buf, start + 14, slave_node.目的MAC地址, 0, 6);
                }
                else
                {
                    slave_node.强制应答标志 = "强制应答";
                    if ((byte)comFunc.BitField16(tmp16, 12, 1) == 1)
                    {
                        slave_node.状态字段 = "STA正在搜表";
                    }
                    else
                    {
                        slave_node.状态字段 = "STA搜表完成";
                    }
                    slave_node.从节点参数注册 = (byte)comFunc.BitField16(tmp16, 13, 3);
                    slave_node.电能表数量 = buf[start + 2];

                    if (buf[start + 3] == 0)
                    {
                        slave_node.产品类型 = "电能表";
                    }
                    else if (buf[start + 3] == 1)
                    {
                        slave_node.产品类型 = "I型采集器";
                    }
                    else if (buf[start + 3] == 2)
                    {
                        slave_node.产品类型 = "II型采集器";
                    }
                    Array.Copy(buf, start + 4, slave_node.设备地址, 0, 6);
                    Array.Copy(buf, start + 10, slave_node.设备ID, 0, 6);
                    slave_node.报文序号 = comFunc.ToUInt32(buf, 4);
                    Array.Copy(buf, start + 24, slave_node.源MAC地址, 0, 6);
                    Array.Copy(buf, start + 30, slave_node.目的MAC地址, 0, 6);
                    Array.Copy(buf, start + 36, slave_node.电能表地址, 0, 6);

                    if (buf[start + 42] == 0)
                    {
                        slave_node.规约类型 = "透明传输";
                    }
                    else if (buf[start + 42] == 1)
                    {
                        slave_node.规约类型 = "DL/T645-1997";
                    }
                    else if (buf[start + 42] == 2)
                    {
                        slave_node.规约类型 = "DL/T645-2007";
                    }
                    else if (buf[start + 42] == 3)
                    {
                        slave_node.规约类型 = "DL/T698-45";
                    }
                    else
                    {
                        slave_node.规约类型 = "保留";
                    }

                    if (comFunc.BitField8(buf[start + 43], 0, 4) == 1)
                    {
                        slave_node.模块类型 = "电能表通讯模块";
                    }
                    else if (comFunc.BitField8(buf[start + 43], 0, 4) == 1)
                    {
                        slave_node.模块类型 = "I型采集器通讯模块";
                    }
                    else if (comFunc.BitField8(buf[start + 43], 0, 4) == 2)
                    {
                        slave_node.模块类型 = "II型采集器通讯模块";
                    }
                    else
                    {
                        slave_node.模块类型 = "保留";
                    }

                }

                aps_mng.帧荷载解析 = (Object)slave_node;
            }

            else if (aps_mng.报文ID == 18)
            {
                aps_mng.具体帧类型 = 8;
                aps_mng.帧类型含义 = "启动从节点主动注册";
                slave_node_active_gw slave_node = new slave_node_active_gw();
                tmp16 = comFunc.ToUInt16(buf, start);
                slave_node.传输方向 = "下行";

                slave_node.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                slave_node.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);
                if ((byte)comFunc.BitField16(tmp16, 12, 1) == 0)
                {
                    slave_node.强制应答标志 = "非强制应答";
                }
                else
                {
                    slave_node.强制应答标志 = "强制应答";
                }


                slave_node.从节点参数注册 = (byte)comFunc.BitField16(tmp16, 13, 3);
                slave_node.报文序号 = comFunc.ToUInt32(buf, start + 4);


                aps_mng.帧荷载解析 = (Object)slave_node;
            }

            else if (aps_mng.报文ID == 19)
            {
                aps_mng.具体帧类型 = 9;
                aps_mng.帧类型含义 = "停止从节点主动注册";
                slave_node_active_gw slave_node = new slave_node_active_gw();
                tmp16 = comFunc.ToUInt16(buf, start);
                slave_node.传输方向 = "下行";

                slave_node.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                slave_node.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);

                slave_node.报文序号 = comFunc.ToUInt32(buf, start + 4);


                aps_mng.帧荷载解析 = (Object)slave_node;
            }

            else if (aps_mng.报文ID == 32)
            {
                aps_mng.具体帧类型 = 10;
                aps_mng.帧类型含义 = "确认/否认";
                ack_sack_gw ack_sack = new ack_sack_gw();
                tmp16 = comFunc.ToUInt16(buf, start);

                ack_sack.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                ack_sack.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);
                if (comFunc.BitField16(tmp16, 12, 1) == 0)
                {
                    ack_sack.方向位 = "下行";
                }
                else
                {
                    ack_sack.方向位 = "上行";
                }

                if (comFunc.BitField16(tmp16, 13, 1) == 0)
                {
                    ack_sack.确认位 = "否认";
                }
                else
                {
                    ack_sack.确认位 = "确认";
                }

                ack_sack.报文序号 = comFunc.ToUInt16(buf, start + 2);

                aps_mng.帧荷载解析 = (Object)ack_sack;
            }

            else if (aps_mng.报文ID == 64)
            {
                aps_mng.具体帧类型 = 18;
                aps_mng.帧类型含义 = "抄控器CCO";
                ctrl_cco_gw ctrl_cco = new ctrl_cco_gw();

                ctrl_cco.传输方向 = "下行";
                if (buf[start] == 0)
                {
                    ctrl_cco.协议类型 = "Q/GDW10376.2-2019";
                }
                else
                {
                    ctrl_cco.协议类型 = "未定义";
                }

                ctrl_cco.报文头长度 = comFunc.ToUInt16(buf, start + 1);
                Array.Copy(buf, start + 3, ctrl_cco.报文内容, 0, ctrl_cco.报文头长度);


                aps_mng.帧荷载解析 = (Object)ctrl_cco;
            }

            else if (aps_mng.报文ID == 65)
            {
                aps_mng.具体帧类型 = 19;
                aps_mng.帧类型含义 = "抄控器数据透传串口转发";
                ctrl_uart_gw ctrl_uart = new ctrl_uart_gw();

                ctrl_uart.传输方向 = "下行";
                if (buf[start] == 0)
                {
                    ctrl_uart.协议类型 = "透明传输";
                }
                else
                {
                    ctrl_uart.协议类型 = "未定义";
                }
                if (comFunc.BitField8(buf[start + 1], 0, 1) == 0)
                {
                    ctrl_uart.启动标志 = "应答报文";
                }
                else
                {
                    ctrl_uart.启动标志 = "主动报文";
                }
                ctrl_uart.串口波特率 = comFunc.ToUInt32(buf, start + 2);
                ctrl_uart.报文头长度 = comFunc.ToUInt16(buf, start + 10);
                Array.Copy(buf, start + 12, ctrl_uart.报文内容, 0, ctrl_uart.报文头长度);


                aps_mng.帧荷载解析 = (Object)ctrl_uart;
            }

            else if (aps_mng.报文ID == 161)
            {
                aps_mng.具体帧类型 = 21;
                aps_mng.帧类型含义 = "台区互变关系识别";
                curts_identify_gw curts_identify = new curts_identify_gw();

                curts_identify.传输方向 = "下行";
                tmp16 = comFunc.ToUInt16(buf, start);

                curts_identify.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                curts_identify.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);
                if (comFunc.BitField8(buf[start + 1], 4, 1) == 0)
                {
                    curts_identify.方向位 = "下行";
                }
                else
                {
                    curts_identify.方向位 = "上行";
                }

                if (comFunc.BitField8(buf[start + 1], 5, 1) == 0)
                {
                    curts_identify.启动位 = "从动站";
                }
                else
                {
                    curts_identify.启动位 = "启动站";
                }

                if (comFunc.BitField8(buf[start + 1], 6, 2) == 0)
                {
                    curts_identify.采集相位 = "默认相位";
                }
                else if (comFunc.BitField8(buf[start + 1], 6, 2) == 1)
                {
                    curts_identify.采集相位 = "CCO第一出线相位";
                }
                else if (comFunc.BitField8(buf[start + 1], 6, 2) == 2)
                {
                    curts_identify.采集相位 = "CCO第二出线相位";
                }
                else if (comFunc.BitField8(buf[start + 1], 6, 2) == 3)
                {
                    curts_identify.采集相位 = "CCO第三出线相位";
                }

                curts_identify.报文序号 = comFunc.ToUInt16(buf, start + 2);
                Array.Copy(buf, start + 4, curts_identify.MAC地址, 0, 6);
                if (buf[start + 10] == 1)
                {
                    curts_identify.特征类型 = "工频电压特征";
                }
                else if (buf[start + 10] == 2)
                {
                    curts_identify.特征类型 = "工频频率特征";
                }
                else if (buf[start + 10] == 3)
                {
                    curts_identify.特征类型 = "工频周期特征";
                }
                else
                {
                    curts_identify.特征类型 = "未知";
                }

                if (buf[start + 11] == 1)
                {
                    curts_identify.采集类型 = "台区特征采集启动";
                    curts_identify.起始NTB = comFunc.ToUInt32(buf, start + 12);
                    if (curts_identify.特征类型 != "工频周期特征")
                    {
                        curts_identify.采集周期 = buf[start + 16];
                    }
                    curts_identify.采集数量 = buf[start + 17];
                    curts_identify.采集序列号 = buf[start + 18];
                }
                else if (buf[start + 11] == 2)
                {
                    curts_identify.采集类型 = "台区特征信息收集";
                }
                else if (buf[start + 11] == 3)
                {
                    curts_identify.采集类型 = "台区特征信息告知";
                    tmp16 = comFunc.ToUInt16(buf, start + 12);
                    curts_identify.TEI = comFunc.BitField16(tmp16, 0, 11);
                    if (comFunc.BitField16(tmp16, 12, 2) == 0)
                    {
                        curts_identify.采集方式 = "保留";
                    }
                    else if (comFunc.BitField16(tmp16, 12, 2) == 0)
                    {
                        curts_identify.采集方式 = "下降沿采集";
                    }
                    else if (comFunc.BitField16(tmp16, 12, 2) == 0)
                    {
                        curts_identify.采集方式 = "上升沿采集";
                    }
                    else if (comFunc.BitField16(tmp16, 12, 2) == 0)
                    {
                        curts_identify.采集方式 = "双沿采集";
                    }

                    curts_identify.采集序列号 = buf[start + 13];
                    curts_identify.告知总数量 = buf[start + 14];
                    curts_identify.起始采集NTB1 = comFunc.ToUInt32(buf, start + 15);

                    if (curts_identify.特征类型 == "工频电压特征")
                    {
                        curts_identify.第一出线报告数量 = buf[start + 17];
                        curts_identify.第二出线报告数量 = buf[start + 18];
                        curts_identify.第三出线报告数量 = buf[start + 19];
                        for (int i = 0; i < curts_identify.第一出线报告数量; i++)
                        {
                            byte highByte = buf[start + 20 + i];    // 高位字节（比如V1的字节4）
                            byte lowByte = buf[start + 21 + i]; // 低位字节（比如V1的字节5）

                            int digit1 = (highByte & 0xF0) >> 4; // 高位字节的高4位 → 第1位十进制数（千分位，对应XXX.X的第1个X）
                            int digit2 = highByte & 0x0F;        // 高位字节的低4位 → 第2位十进制数（百分位，对应XXX.X的第2个X）
                            int digit3 = (lowByte & 0xF0) >> 4;  // 低位字节的高4位 → 第3位十进制数（十分位，对应XXX.X的第3个X）
                            int digit4 = lowByte & 0x0F;         // 低位字节的低4位 → 第4位十进制数（小数位，对应XXX.X的小数点后1位）
                            double voltage = digit1 * 100 + digit2 * 10 + digit3 * 1 + digit4 * 0.1;

                            curts_identify.第一出线电压值 = $"第{i + 1}个电压值是{voltage}";
                        }

                        for (int i = 0; i < curts_identify.第二出线报告数量; i++)
                        {
                            byte highByte = buf[start + 20 + curts_identify.第二出线报告数量 + i];    // 高位字节（比如V1的字节4）
                            byte lowByte = buf[start + 21 + curts_identify.第二出线报告数量 + i]; // 低位字节（比如V1的字节5）

                            int digit1 = (highByte & 0xF0) >> 4; // 高位字节的高4位 → 第1位十进制数（千分位，对应XXX.X的第1个X）
                            int digit2 = highByte & 0x0F;        // 高位字节的低4位 → 第2位十进制数（百分位，对应XXX.X的第2个X）
                            int digit3 = (lowByte & 0xF0) >> 4;  // 低位字节的高4位 → 第3位十进制数（十分位，对应XXX.X的第3个X）
                            int digit4 = lowByte & 0x0F;         // 低位字节的低4位 → 第4位十进制数（小数位，对应XXX.X的小数点后1位）
                            double voltage = digit1 * 100 + digit2 * 10 + digit3 * 1 + digit4 * 0.1;

                            curts_identify.第二出线电压值 = $"第{i + 1}个电压值是{voltage}";
                        }

                        for (int i = 0; i < curts_identify.第三出线报告数量; i++)
                        {
                            byte highByte = buf[start + 20 + curts_identify.第二出线报告数量 + curts_identify.第二出线报告数量 + i];    // 高位字节（比如V1的字节4）
                            byte lowByte = buf[start + 21 + curts_identify.第二出线报告数量 + curts_identify.第二出线报告数量 + i]; // 低位字节（比如V1的字节5）

                            int digit1 = (highByte & 0xF0) >> 4; // 高位字节的高4位 → 第1位十进制数（千分位，对应XXX.X的第1个X）
                            int digit2 = highByte & 0x0F;        // 高位字节的低4位 → 第2位十进制数（百分位，对应XXX.X的第2个X）
                            int digit3 = (lowByte & 0xF0) >> 4;  // 低位字节的高4位 → 第3位十进制数（十分位，对应XXX.X的第3个X）
                            int digit4 = lowByte & 0x0F;         // 低位字节的低4位 → 第4位十进制数（小数位，对应XXX.X的小数点后1位）
                            double voltage = digit1 * 100 + digit2 * 10 + digit3 * 1 + digit4 * 0.1;

                            curts_identify.第三出线电压值 = $"第{i + 1}个电压值是{voltage}";
                        }
                    }
                    else if (curts_identify.特征类型 == "工频频率特征")
                    {
                        curts_identify.第一出线报告数量 = buf[start + 17];
                        curts_identify.第二出线报告数量 = buf[start + 18];
                        curts_identify.第三出线报告数量 = buf[start + 19];
                        for (int i = 0; i < curts_identify.第一出线报告数量; i++)
                        {
                            byte highByte = buf[start + 20 + i];    // 高位字节（比如V1的字节4）
                            byte lowByte = buf[start + 21 + i]; // 低位字节（比如V1的字节5）

                            int digit1 = (highByte & 0xF0) >> 4; // 高位字节的高4位 → 第1位十进制数（千分位，对应XXX.X的第1个X）
                            int digit2 = highByte & 0x0F;        // 高位字节的低4位 → 第2位十进制数（百分位，对应XXX.X的第2个X）
                            int digit3 = (lowByte & 0xF0) >> 4;  // 低位字节的高4位 → 第3位十进制数（十分位，对应XXX.X的第3个X）
                            int digit4 = lowByte & 0x0F;         // 低位字节的低4位 → 第4位十进制数（小数位，对应XXX.X的小数点后1位）
                            double frequency = digit1 * 10 + digit2 * 1 + digit3 * 0.1 + digit4 * 0.01;

                            curts_identify.第一出线频率值 = $"第{i + 1}个频率值是{frequency}";
                        }

                        for (int i = 0; i < curts_identify.第二出线报告数量; i++)
                        {
                            byte highByte = buf[start + 20 + curts_identify.第二出线报告数量 + i];    // 高位字节（比如V1的字节4）
                            byte lowByte = buf[start + 21 + curts_identify.第二出线报告数量 + i]; // 低位字节（比如V1的字节5）

                            int digit1 = (highByte & 0xF0) >> 4; // 高位字节的高4位 → 第1位十进制数（千分位，对应XXX.X的第1个X）
                            int digit2 = highByte & 0x0F;        // 高位字节的低4位 → 第2位十进制数（百分位，对应XXX.X的第2个X）
                            int digit3 = (lowByte & 0xF0) >> 4;  // 低位字节的高4位 → 第3位十进制数（十分位，对应XXX.X的第3个X）
                            int digit4 = lowByte & 0x0F;         // 低位字节的低4位 → 第4位十进制数（小数位，对应XXX.X的小数点后1位）
                            double frequency = digit1 * 10 + digit2 * 1 + digit3 * 0.1 + digit4 * 0.01;

                            curts_identify.第二出线频率值 = $"第{i + 1}个频率值是{frequency}";
                        }

                        for (int i = 0; i < curts_identify.第三出线报告数量; i++)
                        {
                            byte highByte = buf[start + 20 + curts_identify.第二出线报告数量 + curts_identify.第二出线报告数量 + i];    // 高位字节（比如V1的字节4）
                            byte lowByte = buf[start + 21 + curts_identify.第二出线报告数量 + curts_identify.第二出线报告数量 + i]; // 低位字节（比如V1的字节5）

                            int digit1 = (highByte & 0xF0) >> 4; // 高位字节的高4位 → 第1位十进制数（千分位，对应XXX.X的第1个X）
                            int digit2 = highByte & 0x0F;        // 高位字节的低4位 → 第2位十进制数（百分位，对应XXX.X的第2个X）
                            int digit3 = (lowByte & 0xF0) >> 4;  // 低位字节的高4位 → 第3位十进制数（十分位，对应XXX.X的第3个X）
                            int digit4 = lowByte & 0x0F;         // 低位字节的低4位 → 第4位十进制数（小数位，对应XXX.X的小数点后1位）
                            double frequency = digit1 * 10 + digit2 * 1 + digit3 * 0.1 + digit4 * 0.01;

                            curts_identify.第三出线频率值 = $"第{i + 1}个频率值是{frequency}";
                        }
                    }

                }
                else if (buf[start + 11] == 4)
                {
                    curts_identify.采集类型 = "台区判别结果查询";
                }
                else if (buf[start + 11] == 5)
                {
                    curts_identify.采集类型 = "台区判别结果信息";
                    curts_identify.TEI = comFunc.ToUInt16(buf, start + 12);
                    if (buf[start + 13] == 0)
                    {
                        curts_identify.台区判别过程结束标志 = "识别进行中";
                    }
                    else if (buf[start + 13] == 1)
                    {
                        curts_identify.台区判别过程结束标志 = "识别过程结束";
                    }

                    if (buf[start + 14] == 1)
                    {
                        curts_identify.台区识别结果 = "本台区";
                    }
                    else if (buf[start + 14] == 2)
                    {
                        curts_identify.台区识别结果 = "不是本台区";
                    }
                    else
                    {
                        curts_identify.台区识别结果 = "未知";
                    }

                    Array.Copy(buf, start + 15, curts_identify.正确隶属CCO地址, 0, 6);
                }
                else
                {
                    curts_identify.采集类型 = "未知";
                }



                aps_mng.帧荷载解析 = (Object)curts_identify;
            }

            else if (aps_mng.报文ID == 162)
            {
                aps_mng.具体帧类型 = 22;
                aps_mng.帧类型含义 = "查询ID信息";
                read_id_info_gw read_id_info = new read_id_info_gw();

                byte[] buf2 = new byte[24];
                tmp16 = comFunc.ToUInt16(buf, start);
                read_id_info.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                read_id_info.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);

                if (comFunc.BitField16(tmp16, 12, 1) == 0)
                {
                    read_id_info.方向位 = "下行";

                }
                else if (comFunc.BitField16(tmp16, 12, 1) == 1)
                {
                    read_id_info.方向位 = "上行";
                    read_id_info.ID长度 = buf[start + 4];
                    buf2[0] = 0x01;
                    buf2[1] = 0x02;
                    buf2[2] = 0x9c;
                    buf2[3] = 0x01;
                    buf2[4] = 0xc1;
                    buf2[5] = 0xfb;
                    buf2[6] = buf[start + 11];
                    Array.Copy(buf, start + 12, buf2, 7, 2);//厂商代码
                    Array.Copy(buf, start + 14, buf2, 9, 2);//芯片型号
                    Array.Copy(buf, start + 16, buf2, 12, 5);//设备序列号
                    Array.Copy(buf, start + 21, buf2, 16, 8);//校验码
                    Array.Copy(buf2, 0, read_id_info.ID信息, 0, 24);

                    if (buf[start + 4 + read_id_info.ID长度] == 1)
                    {
                        read_id_info.设备类型 = "抄控器";
                    }
                    else if (buf[start + 4 + read_id_info.ID长度] == 2)
                    {
                        read_id_info.设备类型 = "终端本地通讯单元";
                    }
                    else if (buf[start + 4 + read_id_info.ID长度] == 3)
                    {
                        read_id_info.设备类型 = "单相电表通讯单元";
                    }
                    else if (buf[start + 4 + read_id_info.ID长度] == 4)
                    {
                        read_id_info.设备类型 = "中继器";
                    }
                    else if (buf[start + 4 + read_id_info.ID长度] == 5)
                    {
                        read_id_info.设备类型 = "II型采集器";
                    }
                    else if (buf[start + 4 + read_id_info.ID长度] == 6)
                    {
                        read_id_info.设备类型 = "I型采集器";
                    }
                    else if (buf[start + 4 + read_id_info.ID长度] == 7)
                    {
                        read_id_info.设备类型 = "三相电表通信单元";
                    }

                }

                if (comFunc.BitField16(tmp16, 13, 3) == 0)
                {
                    read_id_info.ID类型 = "芯片ID";
                }
                else if (comFunc.BitField16(tmp16, 13, 3) == 1)
                {
                    read_id_info.ID类型 = "芯片ID";
                }
                else if (comFunc.BitField16(tmp16, 13, 3) == 2)
                {
                    read_id_info.ID类型 = "模块ID";
                }
                read_id_info.报文序号 = comFunc.ToUInt16(buf, start + 2);

                aps_mng.帧荷载解析 = (Object)read_id_info;
            }

            else if (aps_mng.报文ID == 163)
            {
                aps_mng.具体帧类型 = 23;
                aps_mng.帧类型含义 = "精准校时";
                accurate_timing_gw accurate_timing = new accurate_timing_gw();

                tmp16 = comFunc.ToUInt16(buf, start);
                accurate_timing.传输方向 = "下行";
                accurate_timing.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                accurate_timing.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);

                tmp16 = comFunc.ToUInt16(buf, start + 1);
                accurate_timing.转发数据长度 = comFunc.BitField16(tmp16, 4, 12);
                accurate_timing.报文序号 = buf[start + 3];
                accurate_timing.NTB = comFunc.ToUInt32(buf, start + 4);

                aps_mng.帧荷载解析 = (Object)accurate_timing;
            }
        }

        public static void aps_mng_port_12_deal_gw(byte[] buf, int bpsz, int start, ref aps_mng_c_gw aps_mng, ref int simple_flag, ref string detail)
        {
            UInt16 tmp16 = 0;
            if (aps_mng.报文ID == 48)
            {
                aps_mng.具体帧类型 = 11;
                aps_mng.帧类型含义 = "开始升级";
                start_upgrade_gw start_upgrade = new start_upgrade_gw();
                tmp16 = comFunc.ToUInt16(buf, start);
                start_upgrade.传输方向 = "下行";

                start_upgrade.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                start_upgrade.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);

                start_upgrade.升级ID = comFunc.ToUInt32(buf, start + 4);
                start_upgrade.升级时间窗口 = comFunc.ToUInt16(buf, start + 8);
                start_upgrade.升级块大小 = comFunc.ToUInt16(buf, start + 10);
                start_upgrade.升级文件大小 = comFunc.ToUInt32(buf, start + 12);
                start_upgrade.文件CRC校验 = comFunc.ToUInt32(buf, start + 16);

                aps_mng.帧荷载解析 = (Object)start_upgrade;
            }

            if (aps_mng.报文ID == 49)
            {
                aps_mng.具体帧类型 = 12;
                aps_mng.帧类型含义 = "停止升级";
                stop_upgrade_gw stop_upgrade = new stop_upgrade_gw();
                tmp16 = comFunc.ToUInt16(buf, start);
                stop_upgrade.传输方向 = "下行";

                stop_upgrade.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                stop_upgrade.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);

                stop_upgrade.升级ID = comFunc.ToUInt32(buf, start + 4);

                aps_mng.帧荷载解析 = (Object)stop_upgrade;
            }

            if (aps_mng.报文ID == 50)
            {
                aps_mng.具体帧类型 = 13;
                aps_mng.帧类型含义 = "传输文件数据";
                trans_upgrade_gw trans_upgrade = new trans_upgrade_gw();
                tmp16 = comFunc.ToUInt16(buf, start);
                trans_upgrade.传输方向 = "下行";

                trans_upgrade.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                trans_upgrade.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);

                trans_upgrade.数据块大小 = comFunc.ToUInt16(buf, start + 2);
                trans_upgrade.升级ID = comFunc.ToUInt32(buf, start + 4);
                trans_upgrade.数据块编号 = comFunc.ToUInt32(buf, start + 8);

                aps_mng.帧荷载解析 = (Object)trans_upgrade;
            }

            if (aps_mng.报文ID == 52)
            {
                aps_mng.具体帧类型 = 15;
                aps_mng.帧类型含义 = "查询站点升级状态";
                request_station_state_gw request_station_state = new request_station_state_gw();
                tmp16 = comFunc.ToUInt16(buf, start);
                request_station_state.传输方向 = "下行";

                request_station_state.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                request_station_state.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);

                request_station_state.连续查询的块数 = comFunc.ToUInt16(buf, start + 2);
                request_station_state.起始块号 = comFunc.ToUInt32(buf, start + 4);
                request_station_state.升级ID = comFunc.ToUInt32(buf, start + 8);

                aps_mng.帧荷载解析 = (Object)request_station_state;
            }

            if (aps_mng.报文ID == 53)
            {
                aps_mng.具体帧类型 = 16;
                aps_mng.帧类型含义 = "执行升级";
                do_upgrade_gw do_upgrade = new do_upgrade_gw();
                tmp16 = comFunc.ToUInt16(buf, start);
                do_upgrade.传输方向 = "下行";

                do_upgrade.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                do_upgrade.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);

                do_upgrade.等待复位时间 = comFunc.ToUInt16(buf, start + 2);
                do_upgrade.升级ID = comFunc.ToUInt32(buf, start + 4);
                do_upgrade.试运行时间 = comFunc.ToUInt32(buf, start + 8);

                aps_mng.帧荷载解析 = (Object)do_upgrade;
            }

            if (aps_mng.报文ID == 54)
            {
                aps_mng.具体帧类型 = 17;
                aps_mng.帧类型含义 = "查询站点信息";
                request_station_info_gw request_station_info = new request_station_info_gw();
                tmp16 = comFunc.ToUInt16(buf, start);
                request_station_info.传输方向 = "下行";

                request_station_info.协议版本号 = (byte)comFunc.BitField16(tmp16, 0, 6);
                request_station_info.报文头长度 = (byte)comFunc.BitField16(tmp16, 6, 6);

                request_station_info.信息列表元素个数 = buf[start + 3];


                aps_mng.帧荷载解析 = (Object)request_station_info;
            }
        }


        public static void mtad_deal(byte[] buf, byte feature, byte clt_type, ref Object deal)
        {
            UInt16 tmp16;
            UInt16 type;
            int index = 0;
            switch (clt_type)
            {
                case 1: //台区特征采集启动
                    if (buf.Length < 8)
                    {
                        deal = null;
                        return;
                    }
                    ad_satrt_c ad_satrt = new ad_satrt_c();
                    ad_satrt.起始NTB = comFunc.ToUInt32(buf, 0);
                    ad_satrt.采集周期s = buf[4];
                    ad_satrt.采集数量 = buf[5];
                    ad_satrt.采集序列号 = buf[6];
                    ad_satrt.resv = buf[7];
                    deal = (Object)ad_satrt;
                    break;
                case 2: //台区特征信息收集
                    break;
                case 3: //台区特征信息告知
                    if (buf.Length < 12)
                    {
                        deal = null;
                        return;
                    }
                    ad_feat_rpt_c ad_feat_rpt = new ad_feat_rpt_c();
                    tmp16 = comFunc.ToUInt16(buf, 0);
                    ad_feat_rpt.TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                    
                    type = comFunc.BitField16(tmp16, 12, 2);
                    if (type == 0)
                    {
                        ad_feat_rpt.采集方式 += type;
                    }
                    else if (type == 1)
                    {
                        ad_feat_rpt.采集方式 += "下降沿采集";
                    }
                    else if (type == 2)
                    {
                        ad_feat_rpt.采集方式 += "上升沿采集";
                    }
                    else if (type == 3)
                    {
                        ad_feat_rpt.采集方式 += "双沿采集";
                    }
                    ad_feat_rpt.采集方式 += "(仅在特征类型为“工频周期”特征时有效)";
                    ad_feat_rpt.resv = (byte)comFunc.BitField16(tmp16, 14, 2);
                    ad_feat_rpt.采集序列号 = buf[2];
                    ad_feat_rpt.告知总数量 = buf[3];
                    ad_feat_rpt.起始采集NTB1 = comFunc.ToUInt32(buf, 4).ToString(); 
                    ad_feat_rpt.台区特征信息序列1 = new feature_seq_c();
                    ad_feat_rpt.台区特征信息序列1.resv = buf[8];
                    ad_feat_rpt.台区特征信息序列1.出线1报告数量 = buf[9].ToString();
                    ad_feat_rpt.台区特征信息序列1.出线2报告数量 = buf[10].ToString();
                    ad_feat_rpt.台区特征信息序列1.出线3报告数量 = buf[11].ToString();
                    index = 12;
                    for (int i = 0; i < 3; i++)
                    {
                        for (int j = 0; j < buf[9+i]; j++)
                        {
                            if (feature == 1) //电压
                            {
                                int hundredsAndTens = (buf[index+1] >> 4) * 10 + (buf[index + 1] & 0x0F); //百位十位
                                int unitsAndDecimal = (buf[index] >> 4) * 10 + (buf[index] & 0x0F) / 10; //个位小数位
                                int decimalPart = buf[index] & 0x0F;
                                ad_feat_rpt.台区特征信息序列1.出线[i] += (hundredsAndTens * 10 + unitsAndDecimal).ToString("D3") + ".";
                                ad_feat_rpt.台区特征信息序列1.出线[i] += decimalPart.ToString("D1") + "|";
                            }
                            else if (feature == 2) //频率
                            {
                                ad_feat_rpt.台区特征信息序列1.出线[i] += buf[index + 1].ToString("X3") + ".";
                                ad_feat_rpt.台区特征信息序列1.出线[i] += buf[index].ToString("X3") + "|";
                            }
                            else if (feature == 3) //周期
                            {
                                Int16 diff = (Int16)((UInt16)buf[index] | (UInt16)buf[index + 1] << 8);
                                ad_feat_rpt.台区特征信息序列1.出线[i] += diff.ToString() + "|";
                            }
                            index += 2;
                        }
                    }
                    if (type == 3 && feature == 3)
                    {
                        int index_tmp = index + 5;
                        ad_feat_rpt.台区特征信息序列2 = new feature_seq_c();
                        ad_feat_rpt.起始采集NTB2 = comFunc.ToUInt32(buf, index).ToString();
                        ad_feat_rpt.台区特征信息序列2.resv = buf[index+4];
                        ad_feat_rpt.台区特征信息序列2.出线1报告数量 = buf[index+5].ToString();
                        ad_feat_rpt.台区特征信息序列2.出线2报告数量 = buf[index+6].ToString();
                        ad_feat_rpt.台区特征信息序列2.出线3报告数量 = buf[index+7].ToString();
                        index += 8;
                        for (int i = 0; i < 3; i++)
                        {
                            for (int j = 0; j < buf[index_tmp + i]; j++)
                            {
                                Int16 diff = (Int16)((UInt16)buf[index] | (UInt16)buf[index + 1] << 8);
                                ad_feat_rpt.台区特征信息序列2.出线[i] += diff.ToString() + "|";
                                index += 2;
                            }
                        }
                    }
                    deal = (Object)ad_feat_rpt;
                    break;
                case 4: //台区判别结果查询
                    break;
                case 5: //台区判别结果信息
                    ad_result_rpt_c ad_result_rpt = new ad_result_rpt_c();
                    ad_result_rpt.TEI = comFunc.ToUInt16(buf, 0).ToString("X3");
                    if (buf[2] == 0)
                    {
                        ad_result_rpt.台区判别过程结束标志 = "识别进行中";
                    }
                    else if (buf[2] == 1)
                    {
                        ad_result_rpt.台区判别过程结束标志 = "识别过程结束";
                    }
                    else
                    {
                        ad_result_rpt.台区判别过程结束标志 = "保留[" + buf[2] + "]";
                    }

                    if (buf[3] == 0)
                    {
                        ad_result_rpt.台区识别结果 += "未知";
                    }
                    else if (buf[3] == 1)
                    {
                        ad_result_rpt.台区识别结果 += "本台区";
                    }
                    else if (buf[3] == 2)
                    {
                        ad_result_rpt.台区识别结果 += "不是本台区";
                    }
                    else
                    {
                        ad_result_rpt.台区识别结果 += "保留[" + buf[3] + "]";
                    }
                    ad_result_rpt.台区识别结果 += "(当台区识别过程未结束时，该域无意义)";
                    Array.Copy(buf, 4, ad_result_rpt.正确隶属CCO地址, 0, 6);
                    deal = (Object)ad_result_rpt;
                    break;
                case 6: //相位特征采集指示
                    phase_clt_c phase_clt = new phase_clt_c();
                    phase_clt.采集数量 = buf[0];
                    phase_clt.采集序列号 = buf[1];
                    Array.Copy(buf, 2, phase_clt.resv, 0, 2);
                    deal = (Object)phase_clt;
                    break;
                case 7: //相位特征采集告知
                    phase_clt_rpt_c phase_clt_rpt = new phase_clt_rpt_c();
                    tmp16 = comFunc.ToUInt16(buf, 0);
                    phase_clt_rpt.TEI = comFunc.BitField16(tmp16, 0, 12).ToString("X3");
                    type = comFunc.BitField16(tmp16, 12, 2);
                    if (type == 1)
                    {
                        phase_clt_rpt.采集方式 = "下降沿采集";
                    }
                    else if (type == 2)
                    {
                        phase_clt_rpt.采集方式 = "上升沿采集";
                    }
                    else
                    {
                        phase_clt_rpt.采集方式 = "保留[" + type + "]";
                    }
                    phase_clt_rpt.resv = (byte)comFunc.BitField16(tmp16, 14, 2);
                    phase_clt_rpt.采集序列号 = buf[2];
                    phase_clt_rpt.告知总数量 = buf[3];
                    phase_clt_rpt.基准NTB = comFunc.ToUInt32(buf, 4);
                    phase_clt_rpt.resv1 = buf[8];
                    phase_clt_rpt.相线1过零NTB差值数量 = buf[9].ToString();
                    phase_clt_rpt.相线2过零NTB差值数量 = buf[10].ToString();
                    phase_clt_rpt.相线3过零NTB差值数量 = buf[11].ToString();
                    index = 12;
                    if (buf[9] != 0)
                    {
                        for (int i = 0; i < buf[9]; i++)
                        {
                            tmp16 = (UInt16)((UInt16)buf[index] | (UInt16)buf[index + 1] << 8);
                            phase_clt_rpt.相线1过零差值 += tmp16.ToString() + "|";
                            index += 2;
                        }
                    }
                    if (buf[10] != 0)
                    {
                        for (int i = 0; i < buf[10]; i++)
                        {
                            tmp16 = (UInt16)((UInt16)buf[index] | (UInt16)buf[index + 1] << 8);
                            phase_clt_rpt.相线2过零差值 += tmp16.ToString() + "|";
                            index += 2;
                        }
                    }
                    if (buf[11] != 0)
                    {
                        for (int i = 0; i < buf[11]; i++)
                        {
                            tmp16 = (UInt16)((UInt16)buf[index] | (UInt16)buf[index + 1] << 8);
                            phase_clt_rpt.相线3过零差值 += tmp16.ToString() + "|";
                            index += 2;
                        }
                    }
                    deal = (Object)phase_clt_rpt;
                    break;
                default:
                    break;
            }
        }
        public static void upgrade_deal(byte[] buf, int bpsz, int start, ref aps_mng_c aps_mng, ref int simple_flag, ref string detail)
        {
            if (bpsz < (start + 4))
            {
                aps_mng.具体帧类型 = 254;
                return;
            }
            aps_file_trans_c file_trans = new aps_file_trans_c();
            file_trans.文件传输信息ID = buf[start];
            Array.Copy(buf, start+1, file_trans.resv, 0, 3);
            start += 4;
            aps_mng.帧类型含义 = "文件传输";
            switch (file_trans.文件传输信息ID)
            {
                case 0:
                    aps_mng.具体帧类型 = 6;
                    file_trans.ID具体含义 = "下发文件信息";
                    if (aps_mng.传输方向位 == 0)
                    {
                        if (bpsz < (start + 24))
                        {
                            aps_mng.具体帧类型 = 254;
                            return;
                        }
                        trans_info_down_c trans_info_down = new trans_info_down_c();
                        if (buf[start] == 0)
                        {
                            trans_info_down.文件性质 = "清除下装文件";
                        }
                        else if (buf[start] == 2)
                        {
                            trans_info_down.文件性质 = "从节点模块文件";
                        }
                        else if (buf[start] == 3)
                        {
                            trans_info_down.文件性质 = "采集器文件";
                        }
                        else if (buf[start] == 4)
                        {
                            trans_info_down.文件性质 = "终端文件";
                        }
                        else
                        {
                            trans_info_down.文件性质 = "其他文件" + buf[start];
                        }
                        trans_info_down.resv = buf[start + 1];
                        Array.Copy(buf, start + 2, trans_info_down.目的地址, 0, 6);
                        Array.Reverse(trans_info_down.目的地址);
                        trans_info_down.文件总校验 = comFunc.ToUInt32(buf, start + 8).ToString("X8");
                        trans_info_down.文件大小 = comFunc.ToUInt32(buf, start + 12);
                        trans_info_down.文件总段数 = comFunc.ToUInt16(buf, start + 16);
                        trans_info_down.文件传输时间窗min = comFunc.ToUInt16(buf, start + 18);
                        trans_info_down.文件传输ID = comFunc.ToUInt32(buf, start + 20);
                        
                        if(simple_flag == 1)
                        {
                            detail += "|" + trans_info_down.文件性质;
                        }
                        file_trans.文件传输信息 = (Object)trans_info_down;
                    }
                    else
                    {
                        if (bpsz < (start + 8))
                        {
                            aps_mng.具体帧类型 = 254;
                            return;
                        }
                        trans_info_up_c trans_info_up = new trans_info_up_c();
                        trans_info_up.文件传输ID = comFunc.ToUInt32(buf, start);
                        trans_info_up.结果码 = comFunc.ToUInt16(buf, start+4);
                        trans_info_up.错误代码 = comFunc.ToUInt16(buf, start + 6);

                        if (trans_info_up.结果码 == 0)
                        {
                            detail += "|成功";
                        }
                        else if (trans_info_up.结果码 == 1)
                        {
                            detail += "|失败:" + trans_info_up.错误代码;
                        }
                        else
                        {
                            detail += "|其他:" + trans_info_up.结果码;
                        }
                        file_trans.文件传输信息 = (Object)trans_info_up;
                    }
                    break;
                case 1:
                case 5:
                    aps_mng.具体帧类型 = 7;
                    file_trans.ID具体含义 = "下发文件数据";
                    if (aps_mng.传输方向位 == 0)
                    {
                        if (bpsz < (start + 10))
                        {
                            aps_mng.具体帧类型 = 254;
                            return;
                        }
                        trans_data_down_c trans_data_down = new trans_data_down_c();
                        trans_data_down.文件段号 = comFunc.ToUInt16(buf, start);
                        trans_data_down.文件总段数 = comFunc.ToUInt16(buf, start+2);
                        trans_data_down.文件传输ID = comFunc.ToUInt32(buf, start+4);
                        trans_data_down.文件长度 = comFunc.ToUInt16(buf, start + 8);
                        if (simple_flag != 1)
                        {
                            if (file_trans.文件传输信息ID == 5)
                            {
                                file_trans.ID具体含义 = "本地广播文件数据";
                            }
                            if (bpsz >= (start + 10 + trans_data_down.文件长度))
                            {
                                trans_data_down.文件段内容 = new byte[trans_data_down.文件长度];
                                Array.Copy(buf, start + 10, trans_data_down.文件段内容, 0, trans_data_down.文件段内容.Length);
                            }
                        }
                        else
                        {
                            if (file_trans.文件传输信息ID == 5)
                            {
                                detail += "|本地广播";
                            }

                            detail += "|下发文件数据,总段数:" + trans_data_down.文件总段数;
                            detail += "|当前段:" + trans_data_down.文件段号; 
                            if (bpsz < (start + 10 + trans_data_down.文件长度))
                            {
                                detail += "|文件长度有误";
                            }
                        }
                        file_trans.文件传输信息 = (Object)trans_data_down;
                    }
                    else
                    {
                        if (bpsz < (start + 8))
                        {
                            aps_mng.具体帧类型 = 254;
                            return;
                        }
                        trans_data_up_c trans_data_up = new trans_data_up_c();
                        trans_data_up.文件传输ID = comFunc.ToUInt32(buf, start);
                        trans_data_up.结果码 = comFunc.ToUInt32(buf, start + 4);
                        detail += "|文件传输结果:" + trans_data_up.结果码;
                        file_trans.文件传输信息 = (Object)trans_data_up;
                    }
                    break;
                case 2:
                    aps_mng.具体帧类型 = 8;
                    file_trans.ID具体含义 = "查询文件数据包接收状态";
                    if (aps_mng.传输方向位 == 0)
                    {
                        if (bpsz < (start + 8))
                        {
                            aps_mng.具体帧类型 = 254;
                            return;
                        }
                        if (simple_flag != 1)
                        {
                            trans_recv_stat_down_c trans_recv_stat_down = new trans_recv_stat_down_c();
                            trans_recv_stat_down.文件传输ID = comFunc.ToUInt32(buf, start);
                            trans_recv_stat_down.起始段号 = comFunc.ToUInt16(buf, start + 4);
                            trans_recv_stat_down.连续N个文件段状态位 = comFunc.ToUInt16(buf, start + 6);
                            file_trans.文件传输信息 = (Object)trans_recv_stat_down;
                        }
                        else
                        {
                            detail += "|查询文件包接收状态";
                        }
                    }
                    else
                    {
                        if (simple_flag != 1)
                        {
                            trans_recv_stat_up_c trans_recv_stat_up = new trans_recv_stat_up_c();
                            trans_recv_stat_up.文件传输ID = comFunc.ToUInt32(buf, start);
                            trans_recv_stat_up.起始段号 = comFunc.ToUInt16(buf, start + 4);
                            trans_recv_stat_up.文件传输状态 = buf[start + 6];
                            trans_recv_stat_up.resv = buf[start + 7];
                            trans_recv_stat_up.连续N个文件段状态 = new byte[aps_mng.帧长 - 8];
                            Array.Copy(buf, start + 8, trans_recv_stat_up.连续N个文件段状态, 0, aps_mng.帧长 - 8);
                            file_trans.文件传输信息 = (Object)trans_recv_stat_up;
                        }
                        else
                        {
                            detail += "|查询文件包接收状态应答";
                            if (buf[start + 6] == 0)
                            {
                                detail += ",空闲";
                            }
                            else if (buf[start + 6] == 1)
                            {
                                detail += ",文件正在CCO和STA之间传输";
                            }
                            else if (buf[start + 6] == 2)
                            {
                                detail += ",文件已被STA正确接收";
                            }
                            else if (buf[start + 6] == 5)
                            {
                                detail += ",文件传输成功，文件已传送至最终目的设备";
                            }
                            else if (buf[start + 6] == 6)
                            {
                                detail += ",文件传输失败，文件无法传送至最终目的设备";
                            }
                            else
                            {
                                detail += ",保留" + buf[start + 6];
                            }
                        }
                        
                    }
                    break;
                case 3:
                    aps_mng.具体帧类型 = 9;
                    file_trans.ID具体含义 = "文件传输完成通知";
                    if (aps_mng.传输方向位 == 0)
                    {
                        if (bpsz < (start + 6))
                        {
                            aps_mng.具体帧类型 = 254;
                            return;
                        }
                        trans_over_down_c trans_over_down = new trans_over_down_c();
                        trans_over_down.文件传输ID = comFunc.ToUInt32(buf, start);
                        trans_over_down.延时启用时间s = comFunc.ToUInt16(buf, start + 4);
                        detail += "|文件传输完成|延时启用时间s:" + trans_over_down.延时启用时间s;
                        file_trans.文件传输信息 = (Object)trans_over_down;

                    }
                    else
                    {
                        if (bpsz < (start + 8))
                        {
                            aps_mng.具体帧类型 = 254;
                            return;
                        }
                        trans_over_up_c trans_over_up = new trans_over_up_c();
                        trans_over_up.文件传输ID = comFunc.ToUInt32(buf, start);
                        trans_over_up.结果码 = comFunc.ToUInt32(buf, start + 4);
                        detail += "|文件传输通知应答，结果:" + trans_over_up.结果码;
                        file_trans.文件传输信息 = (Object)trans_over_up;
                    }
                    break;
                default:
                    break;
            }
            aps_mng.帧荷载解析 = (Object)file_trans;
        }



        public static void aps_mng_port_13_deal(byte[] buf, int bpsz, int start, ref aps_mng_c aps_mng, ref int simple_flag, ref string detail)
        {
            if (aps_mng.帧类型 == 1)
            {//数据转发帧
                if (aps_mng.业务标识 == 1)
                {
                    if (bpsz < (start + 16))
                    {
                        aps_mng.具体帧类型 = 254;
                        return;
                    }
                    aps_mng.具体帧类型 = 3;
                    if (aps_mng.传输方向位 == 0)
                    {
                        aps_mng.帧类型含义 = "模块数据传输下行";
                    }
                    else
                    {
                        aps_mng.帧类型含义 = "模块数据传输上行";
                    }
                        
                    aps_to_mdl_c aps_to_mdl = new aps_to_mdl_c();
                    Array.Copy(buf, start, aps_to_mdl.源地址, 0, 6);
                    Array.Reverse(aps_to_mdl.源地址);
                    Array.Copy(buf, start + 6, aps_to_mdl.目的地址, 0, 6);
                    Array.Reverse(aps_to_mdl.目的地址);
                    aps_to_mdl.resv = buf[start + 12];
                    if (buf[start + 13] == 0)
                    {
                        aps_to_mdl.业务代码 = "透传645报文";
                    }
                    else if (buf[start + 13] == 1)
                    {
                        aps_to_mdl.业务代码 = "精准对时";
                    }
                    else if (buf[start + 13] == 2)
                    {
                        aps_to_mdl.业务代码 = "负荷曲线";
                    }
                    else
                    {
                        aps_to_mdl.业务代码 =  buf[start + 13].ToString("X2");
                    }
                    aps_to_mdl.数据长度 = comFunc.ToUInt16(buf, start + 14);
                    
                    if (simple_flag != 1)
                    {
                        if (bpsz < (start + 16 + aps_to_mdl.数据长度))
                        {
                            aps_mng.帧荷载解析 = (Object)aps_to_mdl;
                            return;
                        }
                        aps_to_mdl.数据内容 = new byte[aps_to_mdl.数据长度];
                        Array.Copy(buf, start + 16, aps_to_mdl.数据内容, 0, aps_to_mdl.数据内容.Length);
                        if (buf[start + 13] == 2)
                        {
                            if (buf[start + 16] == 2)
                            {
                                string info;
                                aps_to_mdl.负荷曲线 = new load_clt_c();
                                aps_to_mdl.负荷曲线.功能码 = "抄读数据项";
                                if (aps_to_mdl.数据内容[1] == 0x00)
                                {
                                    info = "单相表";
                                }
                                else if (aps_to_mdl.数据内容[1] == 0x01)
                                {
                                    info = "三相表";
                                }
                                else
                                {
                                    info = "未知表";
                                }
                                aps_to_mdl.负荷曲线.表类型 = info;
                                info = aps_to_mdl.数据内容[6].ToString("X1") + "-" +
                                        aps_to_mdl.数据内容[5].ToString("X1") + "-" +
                                        aps_to_mdl.数据内容[4].ToString("X1") + "  " +
                                        aps_to_mdl.数据内容[3].ToString("X1") + ":" +
                                        aps_to_mdl.数据内容[2].ToString("X1");
                                aps_to_mdl.负荷曲线.起始点时间 = info;
                                aps_to_mdl.负荷曲线.采集点数量 = aps_to_mdl.数据内容[7];
                                aps_to_mdl.负荷曲线.采集时间间隔 = aps_to_mdl.数据内容[8];
                                aps_to_mdl.负荷曲线.数据项数量 = aps_to_mdl.数据内容[9];
                                int index = 10;
                                aps_to_mdl.负荷曲线.数据项 = new List<string>();
                                for (int i = 0; i < aps_to_mdl.负荷曲线.数据项数量; i++)
                                {
                                    string ID = "";
                                    byte[] data_info = new byte[4];
                                    for (int j = 0; j < 4; j++)
                                    {
                                        data_info[j] = aps_to_mdl.数据内容[index + 3 - j];
                                    }
                                    ID = comFunc.ByteArryToHexStr_2(data_info);
                                    index += 4;
                                    if (aps_mng.传输方向位 == 1)
                                    {
                                        byte[] id_len = { 2, 3, 3, 3, 2, 4, 4, 3 };
                                        byte[] id_len1 = { 6, 9, 12, 12, 8, 16, 16, 6 };
                                        if ((data_info[2] - 1) > id_len.Length)
                                        {
                                            break;
                                        }

                                        if (data_info[2] < 1 || data_info[2] > 8)
                                            break;

                                        for (int k = 0; k < aps_to_mdl.负荷曲线.采集点数量; k++)
                                        {
                                            int data_num = id_len[data_info[2] - 1];
                                            string id_info = "";
                                            if (data_info[3] == 0xFF)
                                            {
                                                data_num = id_len1[data_info[2] - 1];
                                            }
                                            id_info += " " + comFunc.ByteArryToHexStr_3(aps_to_mdl.数据内容, index, data_num);

                                            ID += "  " + id_info;
                                            //clt_data_info.内容.Add(id_info);
                                            index += data_num;
                                        }

                                    }
                                    aps_to_mdl.负荷曲线.数据项.Add(ID);
                                }
                            }
                            
                        }
                    }
                    else
                    {
                        detail += "|业务:" + aps_to_mdl.业务代码;
                        if (buf[start + 13] == 2) //负荷曲线
                        {
                            if (buf[start + 16]== 1)
                            {
                                detail += ",配置采集间隔:" + buf[start + 17] + "min";
                            }
                            else if (buf[start + 16] == 2)
                            {
                                detail += ",抄读数据";
                            }
                        }
                        if (bpsz < (start + 16 + aps_to_mdl.数据长度))
                        {
                            detail += "|抄读数据长度有误";
                        }
                    }
                    aps_mng.帧荷载解析 = (Object)aps_to_mdl;
                }

            }
            else if (aps_mng.帧类型 == 2)
            {//命令帧
                if (aps_mng.业务标识 == 7)
                {//查询从节点运行状态信息
                    aps_mng.具体帧类型 = 14;
                    if (aps_mng.传输方向位 == 0)
                    {
                        aps_mng.帧类型含义 = "查询从节点运行状态信息";
                        aps_sta_run_info_down_c run_info_down = new aps_sta_run_info_down_c();
                        run_info_down.运行信息列表元素数量 = buf[start];
                        if (simple_flag != 1)
                        {
                            if (run_info_down.运行信息列表元素数量 != 0)
                            {
                                run_info_down.信息元素ID = new string[run_info_down.运行信息列表元素数量];
                                for (int i = 0; i < run_info_down.运行信息列表元素数量; i++)
                                {
                                    if (buf[start+1+i] == 0)
                                    {
                                        run_info_down.信息元素ID[i] = "运行时长";
                                    }
                                    else if (buf[start + 1 + i] == 1)
                                    {
                                        run_info_down.信息元素ID[i] = "过零自检结果";
                                    }
                                    else if (buf[start + 1 + i] == 2)
                                    {
                                        run_info_down.信息元素ID[i] = "串口/485 不通状态";
                                    }
                                    else if (buf[start + 1 + i] == 3)
                                    {
                                        run_info_down.信息元素ID[i] = "上次离网原因";
                                    }
                                    else if (buf[start + 1 + i] == 4)
                                    {
                                        run_info_down.信息元素ID[i] = "复位原因";
                                    }
                                    else
                                    {
                                        run_info_down.信息元素ID[i] = buf[start + 1 + i].ToString();
                                    }
                                }
                            }
                        }
                        else
                        {
                            detail += "|查询状态个数：" + run_info_down.运行信息列表元素数量;
                        }
                        aps_mng.帧荷载解析 = (Object)run_info_down;


                    }
                    else
                    {
                        aps_mng.帧类型含义 = "查询从节点运行状态信息应答";
                        aps_sta_run_info_up_c run_info_up = new aps_sta_run_info_up_c();
                        run_info_up.运行信息列表元素数量 = buf[start];
                        if (simple_flag != 1)
                        {
                            if (run_info_up.运行信息列表元素数量 != 0)
                            {
                                run_info_up.信息元素信息 = new List<run_info_c>();
                                start += 1;
                                for (int i = 0; i < run_info_up.运行信息列表元素数量; i++)
                                {
                                    run_info_c info = new run_info_c();
                                    if (buf[start] == 0)
                                    {
                                        info.元素ID = "运行时长";
                                    }
                                    else if (buf[start] == 1)
                                    {
                                        info.元素ID = "过零自检结果";
                                    }
                                    else if (buf[start] == 2)
                                    {
                                        info.元素ID = "串口/485 不通状态";
                                    }
                                    else if (buf[start] == 3)
                                    {
                                        info.元素ID = "上次离网原因";
                                    }
                                    else if (buf[start] == 4)
                                    {
                                        info.元素ID = "复位原因";
                                    }
                                    else
                                    {
                                        info.元素ID = buf[start].ToString();
                                    }
                                    info.元素数据长度 = buf[start + 1];
                                    info.运行信息数据 = new byte[info.元素数据长度];
                                    Array.Copy(buf, start + 2, info.运行信息数据, 0, info.元素数据长度);
                                    run_info_up.信息元素信息.Add(info);
                                    start += 2 + info.元素数据长度;
                                }
                            }
                        }
                        else
                        {
                            detail += "|应答状态个数：" + run_info_up.运行信息列表元素数量;
                        }
                        aps_mng.帧荷载解析 = (Object)run_info_up;
                    }

                }
                else if(aps_mng.业务标识 == 8)
                {//查询从节点信道信息
                    aps_mng.具体帧类型 = 15;
                    if (aps_mng.传输方向位 == 0)
                    {
                        aps_mng.帧类型含义 = "查询从节点信道信息";
                        aps_sta_channel_info_down_c ch_info_down = new aps_sta_channel_info_down_c();
                        ch_info_down.周边节点起始序号 = comFunc.ToUInt16(buf, start);
                        ch_info_down.查询数量 = buf[start + 2];
                        detail += "|起始序号:" + ch_info_down.周边节点起始序号;
                        detail += "|查询数量:" + ch_info_down.查询数量;
                        aps_mng.帧荷载解析 = (Object)ch_info_down;
                    }
                    else
                    {
                        aps_mng.帧类型含义 = "查询从节点信道信息应答";
                        aps_sta_channel_info_up_c ch_info_up = new aps_sta_channel_info_up_c();
                        ch_info_up.周边节点总数量 = comFunc.ToUInt16(buf, start);
                        ch_info_up.本次上报的周边节点数量 = buf[start+2];
                        if (simple_flag != 1)
                        {
                            if (ch_info_up.本次上报的周边节点数量 != 0)
                            {
                                ch_info_up.周边节点信道信息 = new List<channel_info_c>();
                                start += 3;
                                for (int i = 0; i < ch_info_up.本次上报的周边节点数量; i++)
                                {
                                    channel_info_c channel_info = new channel_info_c();
                                    Array.Copy(buf, start, channel_info.节点地址, 0, 6);
                                    channel_info.节点TEI = comFunc.ToUInt16(buf, start + 6);
                                    channel_info.代理TEI = comFunc.ToUInt16(buf, start + 8);
                                    channel_info.层级 = buf[start + 10];
                                    channel_info.上行通信成功率 = buf[start + 11];
                                    channel_info.下行通信成功率 = buf[start + 12];
                                    channel_info.上下行通信成功率 = buf[start + 13];
                                    channel_info.信噪比 = buf[start + 14];
                                    channel_info.衰减 = buf[start + 15];
                                    start += 16;
                                }
                            }
                        }
                        else
                        {
                            detail += "|周边节点总数量：" + ch_info_up.周边节点总数量;
                            detail += "|应答数量：" + ch_info_up.本次上报的周边节点数量;
                        }
                        aps_mng.帧荷载解析 = (Object)ch_info_up;
                    }
                }
            }
            else if (aps_mng.帧类型 == 3)
            {//主动上报帧
                if (aps_mng.业务标识 == 1) //停上电事件主动上报
                {
                    UInt16 tmp16 = 0;
                    UInt32 tmp32 = 0;
                    aps_mng.具体帧类型 = 21;
                    aps_mng.帧类型含义 = "停上电事件主动上报";
                    aps_power_onoff_c power_onoff = new aps_power_onoff_c();

                    tmp32 = comFunc.ToUInt32(buf, start);
                    power_onoff.帧头长度 = (byte)comFunc.BitField32(tmp32, 0, 6);
                    power_onoff.功能码 = (byte)comFunc.BitField32(tmp32, 6, 6);
                    power_onoff.数据长度 = (byte)comFunc.BitField32(tmp32, 12, 12);
                    Array.Copy(buf, start + 3, power_onoff.resv, 0, 9);
                    if (power_onoff.数据长度 != 0)
                    {
                        power_onoff.数据 = new byte[power_onoff.数据长度];
                        Array.Copy(buf, start + 12, power_onoff.数据, 0, power_onoff.数据长度);
                    }
                    if (simple_flag == 1)
                    {
                        if (aps_mng.传输方向位 == 0)
                        {
                            if (power_onoff.功能码 == 1)
                            {
                                detail += "|CCO应答确认";
                            }
                            else
                            {
                                detail += "|未知功能码:" + power_onoff.功能码;
                            }
                        }
                        else
                        {
                            if (power_onoff.功能码 == 1 || power_onoff.功能码 == 2)
                            {
                                if (power_onoff.数据[0] == 1 || power_onoff.数据[0] == 3)
                                {
                                    detail += "|停电";
                                }
                                else if (power_onoff.数据[0] == 4)
                                {
                                    detail += "|上电";
                                }
                            }

                        }
                    }
                    else
                    {
                        if (aps_mng.传输方向位 == 1)
                        {
                            string temp = "";
                            power_onoff.数据域含义 = new List<string>();
                            if (power_onoff.数据[0] == 1)
                            {
                                
                                temp += "停电(位图)";
                                tmp16 = comFunc.ToUInt16(power_onoff.数据, 1);
                                temp += "|起始TEI:" + tmp16.ToString("X3");
                                power_onoff.数据域含义.Add(temp);
                                temp = "";

                                UInt16 conut = 0; //电表计数值
                                for (int i = 0; i < (power_onoff.数据长度 - 3); i++)
                                {
                                    for(int j = 0; j < 8; j++)
                                    {
                                        if((power_onoff.数据[3+i] & (1<<j)) != 0)
                                        {
                                            temp += "|" + (tmp16 + i * 8 + j).ToString("X3");
                                            conut++;
                                            if (conut >= 20)
                                            {
                                                power_onoff.数据域含义.Add(temp);
                                                temp = "";
                                                conut = 0;
                                            }
                                        }
                                    }
                                    
                                }
                                if (conut != 0)
                                {
                                    power_onoff.数据域含义.Add(temp);
                                    temp = "";
                                    conut = 0;
                                }
                                power_onoff.数据域含义.Add(temp);
                            }
                            else if(power_onoff.数据[0] == 3 || power_onoff.数据[0] == 4)
                            {
                                if (power_onoff.数据[0] == 3)
                                {
                                    temp += "停电事件,";
                                }
                                else
                                {
                                    temp += "上电事件,";
                                }
                                tmp16 = comFunc.ToUInt16(power_onoff.数据, 1);
                                temp += "发生事件电表个数:" + tmp16.ToString();
                                power_onoff.数据域含义.Add(temp);
                                temp = "";

                                UInt16 conut = 0; //电表计数值
                                byte[] addr = new byte[6];
                                for(int i = 0; i < tmp16; i++)
                                {
                                    Array.Copy(power_onoff.数据, 3 + 7 * i, addr, 0, 6);
                                    Array.Reverse(addr);
                                    temp += "|"+comFunc.ByteArryToHexStr_2(addr);
                                    if (power_onoff.数据[3 + 7 * i + 6] == 0)
                                    {
                                        temp += "停电";
                                    }
                                    else
                                    {
                                        temp += "未停电";
                                    }
                                    conut++;
                                    if (conut >= 5)
                                    {
                                        power_onoff.数据域含义.Add(temp);
                                        temp = "";
                                        conut = 0;
                                    }
                                }

                                if (conut != 0)
                                {
                                    power_onoff.数据域含义.Add(temp);
                                    temp = "";
                                    conut = 0;
                                }
                            }
                        }
                    }
                    aps_mng.帧荷载解析 = (Object)power_onoff;
                }
                else if (aps_mng.业务标识 == 2)//通信模块事件主动上报
                {
                    UInt32 tmp32 = 0;
                    aps_mng.具体帧类型 = 22;
                    aps_mng.帧类型含义 = "通信模块事件主动上报";
                    aps_mdl_evt_c mdl_evt = new aps_mdl_evt_c();
                    if (simple_flag != 1)
                    {
                        mdl_evt.帧头长度 = (byte)comFunc.BitField32(tmp32, 0, 6);
                        mdl_evt.功能码 = (byte)comFunc.BitField32(tmp32, 6, 6);
                        mdl_evt.数据长度 = (byte)comFunc.BitField32(tmp32, 12, 12);
                        if (mdl_evt.数据长度 != 0)
                        {
                            mdl_evt.数据 = new byte[mdl_evt.数据长度];
                            Array.Copy(buf, start + 3, mdl_evt.数据, 0, mdl_evt.数据长度);
                        }
                    }
                    aps_mng.帧荷载解析 = (Object)mdl_evt;
                }
            }
            else if (aps_mng.帧类型 == 5)
            {//广播命令帧
                aps_mng.具体帧类型 = 25;
                aps_mng.帧类型含义 = "广播帧";
                aps_brd_c aps_brd = new aps_brd_c();
                Array.Copy(buf, start, aps_brd.源地址, 0, 6);
                Array.Reverse(aps_brd.源地址);
                Array.Copy(buf, start + 6, aps_brd.目的地址, 0, 6);
                Array.Reverse(aps_brd.目的地址);
                if (aps_mng.业务标识 == 7)
                {
                    detail += "|查询从节点运行状态信息";
                }
                else if (aps_mng.业务标识 == 8)
                {
                    detail += "|查询从节点信道信息";
                }
                aps_mng.帧荷载解析 = (Object)aps_brd;
            }
        }


    }

    /*
     * 
     */



}
