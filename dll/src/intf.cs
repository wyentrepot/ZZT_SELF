using DllDesc;
using snifferFrame;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
//using static System.Windows.Forms.VisualStyles.VisualStyleElement.ListView;
using System.Reflection.Emit;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
//using static System.Windows.Forms.VisualStyles.VisualStyleElement.ListView;
//using System.Windows.Forms;

namespace NW
{
    public class NwHPLCAnalysis
    {
        private static String NAME = "GW_SMAnalysis";
        private static String VER = "V1.0.23";

        private static FrmHdrInfo CreateGwProxyHeader(FrmHdrInfo_gw gw)
        {
            return new FrmHdrInfo
            {
                InfoLen = gw.InfoLen,
                Data = gw.Data,
                CRC = gw.CRC,
                RSSI = gw.RSSI,
                ProType = "GW",
                SN = gw.SN,
                ChType = gw.ChType,
                RxFchTime = gw.RxFchTime,
                RxFchTimes = gw.RxFchTimes,
                SFO = gw.SFO
            };
        }

        /// <summary>
        /// 从 MAC 帧中提取有界 APS 应用层字节：端口号、报文ID 与原始字节。
        /// 起始偏移 start 为 APS 层起点（GW 标准帧 16/28，GW 单跳帧 4），
        /// 长度取 MAC 头解析出的 MSDU 长度。边界非法或越界时保持三个字段为 null，
        /// 不复制物理块补齐字节。
        /// </summary>
        private static void ExtractApplicationFields(byte[] mac, int msduLen, int start, SimplDesc desc)
        {
            desc.APP_PORT = null;
            desc.APP_ID = null;
            desc.APP_RAW = null;

            if (mac == null || start < 0 || msduLen < 4)
            {
                return;
            }
            int end = start + msduLen;
            if (end > mac.Length)
            {
                return;
            }

            desc.APP_PORT = mac[start].ToString("X2");
            desc.APP_ID = ((UInt16)(mac[start + 1] | (mac[start + 2] << 8))).ToString("X4");
            desc.APP_RAW = comFunc.ByteArryToHexStr_4(mac, start, msduLen);
        }

        /// <summary>
        /// DLL接口-获取DLL协议版本
        /// </summary>
        /// <param name="name"></param>
        /// <param name="version"></param>
        /// <param name="date"></param>
        public void GetProtocolVersion(out string name, out string version, out string date)
        {
            name = NAME;
            version = VER;
            date = System.IO.File.GetLastWriteTime(this.GetType().Assembly.Location).ToString("yyyy.MM.dd HH:mm:ss");
        }


        /// <summary>
        /// DLL接口-协议帧校验
        /// </summary>
        /// <param name="revPacketBuffer"></param>
        /// <param name="startIndex"></param>
        /// <param name="bufferLen"></param>
        /// <param name="index"></param>
        /// <param name="length"></param>
        public void CheckProtocolInfo(byte[] revPacketBuffer, int startIndex, int bufferLen, out int index, out int length)
        {
            index = startIndex;
            length = 0;
            if (revPacketBuffer == null)
            {
                return;
            }
            if(revPacketBuffer.Length < 5)
            {
                return;
            }
            if ((startIndex < 0) || (startIndex > bufferLen) || (startIndex > revPacketBuffer.Length))
            {
                return;
            }

            if(bufferLen >  revPacketBuffer.Length )
            {
                bufferLen = revPacketBuffer.Length;
            }

            bool start = false;
            bool end = false;
            for (int i = startIndex; i < bufferLen; i++)
            {
                if(revPacketBuffer[i] == 0x7e && (i==0 || i==bufferLen-1)) //新加(i==0 || i==bufferLen-1)防止数据里面有7e提前结束
                {
#if true
                    if (start == false)
                    {
                        start = true;
                        index = i;
                        continue;
                    }
                    else if (end == false)
                    {
                        length = i + 1 - index;
                        return;
                    }
#else
                    if ((bufferLen - i) < 36) //最小长度36
                        return;

                    int payLen = (((int)revPacketBuffer[1] << 8) + (int)revPacketBuffer[2]) >> 6;
                    int infoLen = revPacketBuffer[3];
                    //1(7E) + 3(帧类型+帧长度+信息域长度) + 信息域 + 荷载 + 4(CRC) + 1(7E)
                    int frame_len = 1 + 3 + infoLen + payLen + 4 + 1;
                    if (frame_len > (bufferLen - i))
                    {
                        return;
                    }

                    if (revPacketBuffer[i + frame_len - 1] == 0x7E)
                    {
                        index = i;
                        length = frame_len;
                        return;
                    }

                    //可能有转义
                    for (int j = 0; j < (bufferLen-i); j++)
                    {
                        if (revPacketBuffer[i+j] == 0x7D)
                        {
                            if (revPacketBuffer[i +j + 1] == 0x5e || revPacketBuffer[i + j + 1] == 0x5d)
                            {//转义了
                                frame_len++;
                                if (revPacketBuffer[i + frame_len - 1] == 0x7E)
                                {
                                    index = i;
                                    length = frame_len;
                                    return;
                                }
                            }
                        }
                    }
                    
                    i++;
#endif
                }
            }
        }

        /// <summary>
        /// DLL接口-简单解析，用于表格显示
        /// </summary>
        /// <param name="packet"></param>
        /// <param name="length"></param>
        /// <param name="jsonDesc"></param>
        public void GetProtocolSimpleDesc(byte[] packet, int length, out string jsonDesc)
        {
            int simple_flag = 1;
            SimplDesc desc = new SimplDesc();

            //帧预处理
            int res = sniffer.FrmPreprc(packet, length, out FrmHdrInfo HdrInfo, out byte[] Payload);
            int res_gw = sniffer.FrmPreprc_gw(packet, length, out FrmHdrInfo_gw HdrInfo1, out byte[] Payload1);

            if (res < 0 && res_gw < 0)  //帧错误
            {
                desc.FrmType = "ERROR";
                if (res == -1)
                {
                    desc.Detail = "包长度小于10";
                }
                else if (res == -2)
                {
                    desc.Detail = "帧长度小于10";
                }
                else if(res == -3)
                {
                    desc.Detail = "实际帧长度小于帧长度";
                }
                jsonDesc = new JavaScriptSerializer().Serialize(desc);
                return;
            }

            bool isGwFrame = res_gw == 0 && HdrInfo1 != null && HdrInfo1.ProType == "GW";
            if (isGwFrame && HdrInfo == null)
            {
                HdrInfo = CreateGwProxyHeader(HdrInfo1);
            }

            //国网帧预处理
            /*int res_gw = sniffer.FrmPreprc_gw(packet, length, out FrmHdrInfo_gw HdrInfo1, out byte[] Payload1);
              if (res_gw < 0)  //帧错误
              {
                  desc_gw.FrmType = "ERROR";
                  if (res_gw == -1)
                  {
                      desc_gw.Detail = "包长度小于10";
                  }
                  else if (res_gw == -2)
                  {
                      desc_gw.Detail = "帧长度小于10";
                  }
                  else if (res_gw == -3)
                  {
                      desc_gw.Detail = "实际帧长度小于帧长度";
                  }
                  jsonDesc = new JavaScriptSerializer().Serialize(desc_gw);
                  return;
              }*/
            
           /* if(HdrInfo1.ProType == "GW" || HdrInfo.ProType == "GW")
            {
                Payload = null;
                HdrInfo = null;
            }*/


            desc.Info = HdrInfo;
            desc.Info2 = HdrInfo1;
            if (isGwFrame)
            {
                desc.FrmType = "国网";
            }

            

            //FCH获取
            byte[] fch = new byte[16];
            if(HdrInfo.ProType == "GW")
            {
              Array.Copy(Payload1, fch, fch.Length);
            }
            else
            {
               Array.Copy(Payload, fch, fch.Length);
            }

            int frmType = fch[0] & 0x07;
            byte nid = (byte)((fch[0] >> 4) & 0x0F);
            //缺少帧控制校验
            byte[] mpdu;
            UInt32 mpdu_len = 0;

            byte std_ver = (byte)((fch[12] >> 4) & 0x0F);
            if (std_ver == 0)
            {
                desc.FrmType = "国网";
                jsonDesc = new JavaScriptSerializer().Serialize(desc);
                // return;
            }

            switch (frmType)
            {
                case 0:  //信标帧
                    UInt32 bcn_cnt = 0;
                    ctrl_beacon ctrl_bcn;
                    csma_union_c csma_union = new csma_union_c();
                    csma_union_c_gw csma_union_gw = new csma_union_c_gw();
                    //国网信标帧
                    ctrl_beacon_gw ctrl_bcn_gw;
                    //#if NWSM
                    ctrl_beacon_rf ctrl_bcn_rf;

                    ctrl_beacon_rf_gw ctrl_bcn_rf_gw;

                    if (desc.FrmType == "国网" && HdrInfo1.ChType == "无线")
                    {
                        ctrl_bcn_rf_gw = Parse.fch_bcn_rf_gw(fch, ref simple_flag);
                        desc.SRC = ctrl_bcn_rf_gw.源TEI;
                        //bcn_cnt = ctrl_bcn_rf_gw.信标周期计数;
                        mpdu_len = ctrl_bcn_rf_gw.载荷PB大小;
                        desc.Detail += " RF";
                        //desc.Detail += "|信标数" + bcn_cnt;
                    }
                    else if ((desc.FrmType == "国网" && HdrInfo1.ChType == "载波"))
                    {
                            ctrl_bcn_gw = Parse.fch_bcn_gw(fch, ref simple_flag);
                            desc.SRC = ctrl_bcn_gw.源TEI;
                            mpdu_len = (UInt32)(Payload1.Length - 16);
                           
                    }

                    if (HdrInfo!=null && HdrInfo.ProType == "NW" && HdrInfo.ChType == "HRF")
                    {
                            ctrl_bcn_rf = Parse.fch_bcn_rf(fch, ref simple_flag);
                            desc.SRC = ctrl_bcn_rf.源TEI;
                            bcn_cnt = ctrl_bcn_rf.信标周期计数;
                            mpdu_len = ctrl_bcn_rf.载荷PB大小;
                            desc.Detail += " RF";
                            desc.Detail += "|信标数" + bcn_cnt;
                    }
                    else if(HdrInfo != null && HdrInfo.ProType == "NW")
                        //#endif
                    {
                            ctrl_bcn = Parse.fch_bcn(fch, ref simple_flag);
                            desc.SRC = ctrl_bcn.源TEI;
                            desc.Detail += ctrl_bcn.相线;
                            bcn_cnt = ctrl_bcn.信标周期计数;
                            mpdu_len = (UInt32)(Payload.Length - 16);
                            desc.Detail += "|信标数" + bcn_cnt;
                    }


                        if (HdrInfo != null && HdrInfo.CRC == "ERROR")
                        {
                            desc.FrmType = "ERR";
                            desc.Detail = "CRC error";
                            break;
                        }
                    

                    if (mpdu_len == 0)
                    {
                        desc.FrmType = "ERR";
                        break;
                    }

                    mpdu = new byte[mpdu_len];
                    if (HdrInfo.ProType == "GW")
                    {
                        Array.Copy(Payload1, 16, mpdu, 0, mpdu.Length);
                    }
                    else
                    {
                        Array.Copy(Payload, 16, mpdu, 0, mpdu.Length);
                    }
                    desc.frame_len = mpdu.Length;

//#if NWSM
                    if (desc.FrmType == "国网" && comFunc.BitField8(mpdu[0], 4, 1) == 1) //国网精简信标处理
                    {
                     
                       beacon_pld_jj_gw bcn_jj_gw = Parse.bcn_payload_jj_gw(mpdu, mpdu.Length, ref simple_flag, ref desc.Detail);
                        
                        desc.FrmType = bcn_jj_gw.信标类型;
                        if (bcn_jj_gw.站点能力及时隙条目 != null)
                       {
                            desc.ADDR = comFunc.ByteArryToHexStr_2(bcn_jj_gw.站点能力及时隙条目.发送信标站点MAC地址);
                       }
                        desc.TS_TYPE = TimeSlotClass.bcn_slot_cal(ref nid, ref desc.Info2.RxFchTime);
                        break;
                    }
                    else if (HdrInfo != null && comFunc.BitField8(mpdu[0], 4, 1) == 1) //南网精简信标处理
                    {
                        beacon_pld_jj bcn_jj = Parse.bcn_payload_jj(mpdu, mpdu.Length, ref simple_flag, ref desc.Detail);
                        desc.FrmType = bcn_jj.信标类型;
                        
                        if (bcn_jj.站点能力及时隙条目 != null)
                        {
                            desc.ADDR = comFunc.ByteArryToHexStr_2(bcn_jj.站点能力及时隙条目.发送信标站点MAC地址);
                        }
                        desc.TS_TYPE = TimeSlotClass.bcn_slot_cal(ref nid, ref desc.Info.RxFchTime);
                        break;
                    }
                    //#endif            
                    if (HdrInfo.ProType == "NW")
                    {
                        beacon_pld bcn = Parse.bcn_payload(mpdu, mpdu.Length, ref simple_flag, ref csma_union, ref desc.Detail);

                        if (HdrInfo.ProType == "NW")
                        {
                            TimeSlotClass.bcn_info_extract(ref bcn, ref csma_union, ref bcn_cnt);
                            desc.TS_TYPE = TimeSlotClass.bcn_slot_cal(ref nid, ref desc.Info.RxFchTime);
                        }

                        desc.FrmType = bcn.信标类型;
                        if (bcn.站点能力条目 != null)
                        {
                            desc.ADDR = comFunc.ByteArryToHexStr_2(bcn.站点能力条目.发送信标站点MAC地址);
                        }
                        if (bcn.时隙分配条目 != null)
                        {
                            desc.BCN_S = bcn.时隙分配条目.NTB;
                        }
                    }
                    else
                    {
                        beacon_pld_gw bcn = Parse.bcn_payload_gw(mpdu, mpdu.Length, ref simple_flag, ref csma_union_gw, ref desc.Detail);

                        if (HdrInfo.ProType == "GW")
                        {
                            TimeSlotClass.bcn_info_extract_gw(ref bcn, ref csma_union_gw, ref bcn_cnt);
                            desc.TS_TYPE = TimeSlotClass.bcn_slot_cal(ref nid, ref desc.Info2.RxFchTime);
                        }

                        desc.FrmType = bcn.信标类型;
                        if (bcn.站点能力条目 != null)
                        {
                            desc.ADDR = comFunc.ByteArryToHexStr_2(bcn.站点能力条目.发送信标站点MAC地址);
                        }
                        if (bcn.时隙分配条目 != null)
                        {
                            desc.BCN_S = bcn.时隙分配条目.NTB;
                        }
                    }
                    break;
                case 1: //SOF帧
                    int pb_size = 0;
                    int pb_num = 0;
                    ctrl_sof ctl_sof;
                    ctrl_sof_gw ctl_sof_gw;
//#if NWSM           
                    ctrl_sof_rf ctl_sof_rf;
                    ctrl_sof_rf_gw ctl_sof_rf_gw;

                    if (desc.FrmType == "国网" && HdrInfo1.ChType == "无线")
                    {
                        ctl_sof_rf_gw = Parse.fch_sof_rf_gw(fch, ref simple_flag);
                        desc.SRC = ctl_sof_rf_gw.源TEI;
                        desc.DST = ctl_sof_rf_gw.目的TEI;
                        mpdu_len = ctl_sof_rf_gw.载荷PB大小;
                        pb_size = (int)mpdu_len;
                        pb_num = 1;
                        desc.TS_TYPE = "CSMA";
                    }
                    else if ((desc.FrmType == "国网" && HdrInfo1.ChType == "载波"))
                    {
                        ctl_sof_gw = Parse.fch_sof_gw(fch, ref simple_flag);
                        desc.SRC = ctl_sof_gw.源TEI;
                        desc.DST = ctl_sof_gw.目的TEI;
                        desc.TS_TYPE = TimeSlotClass.sof_slot_cal(ref nid, ref desc.Info2.RxFchTime, ref desc.Debug);
                        mpdu_len = (UInt32)(Payload1.Length - 16);
                        pb_size = Parse.MPDU_check_gw(Payload1, Payload1.Length);
                        pb_num = ctl_sof_gw.物理块个数;

                    }

                    if (HdrInfo != null && HdrInfo.ProType == "NW" && HdrInfo.ChType == "HRF")
                    {
                        ctl_sof_rf = Parse.fch_sof_rf(fch, ref simple_flag);
                        desc.SRC = ctl_sof_rf.源TEI;
                        desc.DST = ctl_sof_rf.目的TEI;
                        mpdu_len = ctl_sof_rf.载荷PB大小;
                        pb_size = (int)mpdu_len;
                        pb_num = 1;
                        desc.TS_TYPE = "CSMA";
                    }
                    else if(HdrInfo.ProType == "NW")
//#endif
                    {
                        ctl_sof = Parse.fch_sof(fch, ref simple_flag);
                        desc.SRC = ctl_sof.源TEI;
                        desc.DST = ctl_sof.目的TEI;
                        desc.TS_TYPE = TimeSlotClass.sof_slot_cal(ref nid, ref desc.Info.RxFchTime, ref desc.Debug);
                        mpdu_len = (UInt32)(Payload.Length - 16);
                        pb_size = Parse.MPDU_check(Payload, Payload.Length);
                        pb_num = ctl_sof.物理块个数;
                    }
                 

                    if (HdrInfo != null && HdrInfo.CRC == "ERROR")
                    {
                        desc.FrmType = "ERR";
                        desc.Detail = "CRC error";
                        break;
                    }

                    if (mpdu_len == 0)
                    {
                        desc.FrmType = "ERR";
                        break;
                    }


                    if (HdrInfo.ProType == "GW")
                    {
                        mpdu = new byte[Payload1.Length - 16];
                        Array.Copy(Payload1, 16, mpdu, 0, mpdu.Length);
                    }
                    else
                    {
                        mpdu = new byte[Payload.Length - 16];
                        Array.Copy(Payload, 16, mpdu, 0, mpdu.Length);
                    }

                    int expectedMacLength = (pb_size > 8 && pb_num > 0) ? (pb_size - 8) * pb_num : 0;
                    if (HdrInfo.ProType == "GW" && expectedMacLength > mpdu.Length)
                    {
                        desc.FrmType = "SOF帧";
                        desc.frame_len = mpdu.Length;
                        desc.Detail = "国网物理块长度不足，已解析侦听台头和FCH；"
                            + "期望MAC长度" + expectedMacLength.ToString()
                            + "，实际" + mpdu.Length.ToString();
                        break;
                    }

                    if (pb_size > 0)
                    {
                        byte[] mac = new byte[(pb_size - 8) * pb_num];
                        for (int i = 0; i < pb_num; i++)
                        {
                            if (HdrInfo.ProType == "GW")
                            {
                                Array.Copy(Payload1,  16 , mac, i * (pb_size - 8), pb_size - 8); //提取MAC帧
                            }
                            else
                            {
                                Array.Copy(Payload, 4 + 16 + i * pb_size + 4, mac, i * (pb_size - 8), pb_size - 8); //提取MAC帧
                            }
                            
                        }

                        if (Parse.MAC_check(mac, mac.Length) > 0)
                        {
                            desc.frame_len = mac.Length;
                            sof_pld_c sof;
                            desc.FrmType = "SOF帧";
//#if NWSM
                            if (HdrInfo1.ProType == "NW" && comFunc.BitField8(mac[0], 1, 2) == 2) //单跳帧协议
                            {
                                sof = Parse.msdu_sof_single(mac, mac.Length, ref simple_flag, ref desc.Detail);
                                if (sof.MAC单跳帧头.MSDU类型 == 1)
                                {
                                    Parse.msdu_sof_s_para_extract(ref sof, ref desc.FrmType, ref desc.ADDR);
                                }
                                else if (sof.MAC单跳帧头.MSDU类型 == 2)
                                {
                                    MMeRfDiscoverList_c rfDiscoverList = (MMeRfDiscoverList_c)sof.单跳帧;
                                    desc.ADDR = comFunc.ByteArryToHexStr_2(rfDiscoverList.站点MAC地址);
                                    desc.FrmType = "无线发现列表";
                                }
                                else
                                {
                                    desc.FrmType = "Err";
                                    desc.Detail = "Uknow";
                                }
                                break;
                            }

                            if (HdrInfo1.ProType == "GW" && comFunc.BitField8(mac[0], 0, 4) == 1) //国网单跳帧协议
                            {
                                sof = Parse.msdu_sof_single_gw(mac, mac.Length, ref simple_flag, ref desc.Detail);
                                if (sof.国网MAC单跳帧头.消息类型 == 128)
                                {
                                    Parse.msdu_sof_s_para_extract_gw(ref sof, ref desc.FrmType, ref desc.ADDR);
                                    ExtractApplicationFields(mac, sof.国网MAC单跳帧头.MSDU长度, 4, desc);
                                }
                                else if (sof.国网MAC单跳帧头.消息类型 == 0)
                                {
                                    MMeRfDiscoverList_c rfDiscoverList = (MMeRfDiscoverList_c)sof.单跳帧;
                                    desc.ADDR = comFunc.ByteArryToHexStr_2(rfDiscoverList.站点MAC地址);
                                    desc.FrmType = "无线发现列表";
                                }
                                else
                                {
                                    desc.FrmType = "Err";
                                    desc.Detail = "Uknow";
                                }
                                break;
                            }
//#endif
                            if (HdrInfo.ProType == "NW" && comFunc.BitField8(mac[0], 0, 1) == 0) //长帧头
                            {
                                sof = Parse.msdu_sof_l(mac, mac.Length, ref simple_flag, ref desc.Detail);
                                desc.Msdu_seq = sof.MAC长帧头.MSDU序列号;
                                desc.ORI_S = sof.MAC长帧头.原始源TEI;
                                desc.FINL_D = sof.MAC长帧头.原始目的TEI;
                                Parse.msdu_sof_l_para_extract(ref sof, ref desc.FrmType, ref desc.ADDR);
                            }
                            else if(HdrInfo.ProType == "NW")
                            {
                                sof = Parse.msdu_sof_s(mac, mac.Length, ref simple_flag, ref desc.Detail);
                                desc.Msdu_seq = sof.MAC短帧头.MSDU序列号;
                                desc.ORI_S = sof.MAC短帧头.原始源TEI;
                                desc.FINL_D = sof.MAC短帧头.原始目的TEI;
                                Parse.msdu_sof_s_para_extract(ref sof, ref desc.FrmType, ref desc.ADDR);
                            }
                            if (HdrInfo1.ProType == "GW" && comFunc.BitField8(mac[0], 0, 4) == 0)
                            {
                                sof = Parse.msdu_sof_gw(mac, mac.Length, ref simple_flag, ref desc.Detail);
                                desc.Msdu_seq = sof.GW标准帧.MSDU序列号;
                                desc.ORI_S = sof.GW标准帧.原始源TEI;
                                desc.FINL_D = sof.GW标准帧.原始目的TEI;
                                if (sof.GW标准帧.MSDU类型 == "网络消息管理报文")
                                {
                                    Parse.msdu_sof_l_para_extract_gw(ref sof, ref desc.FrmType, ref desc.ADDR);
                                }
                                    
                                if(sof.GW标准帧.MSDU类型 == "应用层报文")
                                {
                                    Parse.msdu_sof_s_para_extract_gw(ref sof, ref desc.FrmType, ref desc.ADDR);
                                    int apsStart = (sof.GW标准帧.mac地址标志 == 1) ? 28 : 16;
                                    ExtractApplicationFields(mac, sof.GW标准帧.MSDU长度, apsStart, desc);
                                }
                            }
                            
                        }
                        else
                        {
                            desc.FrmType = "MAC_err";
                        }
                        
                    }
                    else
                    {
                        desc.FrmType = "MPDU_err";
                    }
                    break;
                case 2: //SACK帧
                    byte protype = 0;
                  desc.FrmType = "SACK";
                    if(HdrInfo1.ProType == "GW" && HdrInfo1.ChType == "无线")
                    {

                        desc.TS_TYPE = "CSMA";
                        ctrl_sack_rf_gw sack_rf_gw = Parse.fch_sack_rf_gw(fch, protype, ref desc.Detail);

                        if (sack_rf_gw.扩展帧类型 == 0)
                        {
                            desc.FrmType = "ACK";
                            desc.DST = sack_rf_gw.目的TEI;
                        }

                        break;

                    }
                    else if ((HdrInfo1.ProType == "GW" && HdrInfo1.ChType == "载波"))
                    {
                        desc.TS_TYPE = TimeSlotClass.sof_slot_cal(ref nid, ref desc.Info2.RxFchTime, ref desc.Debug);
                        ctrl_sack_gw sack_gw = Parse.fch_sack_gw(fch, protype, ref desc.Detail);

                        if (sack_gw.扩展帧类型 == 0)
                        {
                            desc.FrmType = "ACK";
                            desc.DST = sack_gw.目的TEI;
                        }

                        break;
                    }

                    if (HdrInfo != null && HdrInfo.ProType == "NW" && HdrInfo.ChType == "HRF")
                    {
                        protype = 1;
                        desc.TS_TYPE = "CSMA";
                    }
                    else if(HdrInfo.ProType == "NW")
                    {
                        desc.TS_TYPE = TimeSlotClass.sof_slot_cal(ref nid, ref desc.Info.RxFchTime, ref desc.Debug);
                    }
                    ctrl_sack sack = Parse.fch_sack(fch, protype, ref desc.Detail);
                    if (sack.扩展帧类型 == 0)
                    {
                        desc.FrmType = "ACK";
                        if (protype == 1)
                        {
                            desc.DST = sack.ACK_RF.目的TEI;
                        }
                        else
                        {
                            desc.DST = sack.ACK.目的TEI;
                        }
                        
                    }
                    else if (sack.扩展帧类型 == 1)
                    {
                        desc.FrmType = "网络搜索帧";
                        desc.SRC= sack.网络搜索帧.源TEI;
                    }
                    else if (sack.扩展帧类型 == 2)
                    {
                        desc.FrmType = "同步帧";
                        desc.SRC = sack.同步帧.源TEI;
                    }
                    else if (sack.扩展帧类型 == 10)
                    {
                        desc.FrmType = "时隙预约帧";
                        desc.SRC = sack.时隙预约帧.源TEI;
                    }

                    break;
                case 3: //网间协调
                    if(desc.FrmType == "国网")
                    {
                        desc.FrmType = "网间协调";
                        ctrl_coord_gw coord_gw = Parse.fch_ccord_gw(fch);
                        desc.Detail += "邻居网络:" + coord_gw.接收到的邻居网络号;
                        desc.Detail += "|持续时间" + coord_gw.持续时间ms + "ms";
                        desc.Detail += "|带宽开始偏移" + coord_gw.带宽开始偏移ms + "ms";
                        desc.Detail += "|无线信道号：" + coord_gw.本网络无线信道编号;
                        desc.TS_TYPE = TimeSlotClass.sof_slot_cal(ref nid, ref desc.Info2.RxFchTime, ref desc.Debug);
                        break;
                    }

                    if (HdrInfo != null && HdrInfo.ProType == "NW" && HdrInfo.ChType == "HRF")
                    {
                        desc.TS_TYPE = "CSMA";
                    }
                    else if (HdrInfo.ProType == "NW")
                    {
                        desc.TS_TYPE = TimeSlotClass.sof_slot_cal(ref nid, ref desc.Info.RxFchTime, ref desc.Debug);
                    }
                    desc.FrmType = "网间协调";
                    ctrl_coord coord = Parse.fch_ccord(fch);
                    desc.Detail += "邻居网络:" + coord.邻居网络;
                    desc.Detail += "|持续时间" + coord.持续时间ms + "ms";
                    desc.Detail += "|带宽结束偏移" + coord.带宽结束偏移ms + "ms";
                    desc.Detail += "|带宽开始偏移" + coord.带宽开始偏移ms + "ms";
                    desc.Detail += "|无线信道号：" + coord.本网络无线信道编号;
                    desc.Detail += "|option：" + coord.本网络无线option;
                    desc.TS_TYPE = TimeSlotClass.sof_slot_cal(ref nid, ref desc.Info.RxFchTime, ref desc.Debug);
                    break;
                default:
                    desc.FrmType = "无效";
                    break;
            }
            //desc.SNID = (fch[0] >>4) & 0x0F;
            desc.SNID = comFunc.ByteArryToHexStr_3(fch, 1, 3).PadLeft(8, '0');
            jsonDesc = new JavaScriptSerializer().Serialize(desc);
        }
       
        /// <summary>
        /// DLL接口-全功能解析，用于详细展示帧内容
        /// </summary>
        /// <param name="packet"></param>
        /// <param name="length"></param>
        /// <param name="jsonDesc"></param>
        public void GetProtocolFullDesc(byte[] packet, int length, out string jsonDesc)
        {
            int simple_flag = 0;
            FullDesc desc = new FullDesc();

            //帧预处理
            int res = sniffer.FrmPreprc(packet, length, out FrmHdrInfo HdrInfo, out byte[] Payload);
            int res_gw = sniffer.FrmPreprc_gw(packet, length, out FrmHdrInfo_gw HdrInfo1, out byte[] Payload1);

            if (res < 0 && res_gw < 0)  //帧错误
            {
                desc.Error = "帧错误:" + res.ToString();
                jsonDesc = new JavaScriptSerializer().Serialize(desc);
                return;
            }

            bool isGwFrame = res_gw == 0 && HdrInfo1 != null && HdrInfo1.ProType == "GW";
            if (isGwFrame && HdrInfo == null)
            {
                HdrInfo = CreateGwProxyHeader(HdrInfo1);
            }

            /*if (res_gw < 0)  //帧错误
             {
                 jsonDesc = "{" + "帧错误:" + res_gw.ToString() + "}";
                 return;
             }*/


            /*if (HdrInfo1.ProType == "GW" || HdrInfo.ProType == "GW")
            {
                Payload = null;
                HdrInfo = null;
            }*/

            desc.Info = HdrInfo;
            desc.Info2 = HdrInfo1;
            //FCH获取
            byte[] fch = new byte[16];
            if (HdrInfo.ProType == "GW")
            {
                Array.Copy(Payload1, fch, fch.Length);
            }
            else
            {
                Array.Copy(Payload, fch, fch.Length);
            }
            int frmType = fch[0] & 0x07;


            byte[] mpdu;
            UInt32 mpdu_len = 0;
            string detail = "";

            /*byte std_ver = (byte)((fch[12] >> 4) & 0x0F);
            if (std_ver == 0)
            {
                jsonDesc = new JavaScriptSerializer().Serialize(desc);
                return;
            }*/

            //FCH解析
            switch (frmType)
            {
                case 0:   //信标帧
                    UInt32 bcn_cnt = 0;
                    ctrl_beacon ctrl_bcn;
                    ctrl_beacon_gw ctrl_bcn_gw;
                    csma_union_c csma_union = new csma_union_c();
                    csma_union_c_gw csma_union_gw = new csma_union_c_gw();
                    //#if NWSM             
                    ctrl_beacon_rf ctrl_bcn_rf;
                    ctrl_beacon_rf_gw ctrl_bcn_rf_gw;
                    if (HdrInfo.ProType == "GW" && HdrInfo1.ChType == "无线")
                    {
                        ctrl_bcn_rf_gw = Parse.fch_bcn_rf_gw(fch, ref simple_flag);
                        //bcn_cnt = ctrl_bcn_rf.信标周期计数;
                        mpdu_len = ctrl_bcn_rf_gw.载荷PB大小;
                        desc.FCH = (Object)ctrl_bcn_rf_gw;
                    }
                    else if (HdrInfo.ProType == "GW" && HdrInfo1.ChType == "载波")
                    {
                        ctrl_bcn_gw = Parse.fch_bcn_gw(fch, ref simple_flag);
                        //bcn_cnt = ctrl_bcn.信标周期计数;
                        mpdu_len = (UInt32)(Payload1.Length - 16);
                        desc.FCH = (Object)ctrl_bcn_gw;


                    }

                    if (HdrInfo != null && HdrInfo.ProType == "NW" && HdrInfo.ChType == "HRF")
                    {
                        ctrl_bcn_rf = Parse.fch_bcn_rf(fch, ref simple_flag);
                        bcn_cnt = ctrl_bcn_rf.信标周期计数;
                        mpdu_len = ctrl_bcn_rf.载荷PB大小;
                        desc.FCH = (Object)ctrl_bcn_rf;
                    }
                    else if (HdrInfo.ProType == "NW")
//#endif
                    {
                        ctrl_bcn = Parse.fch_bcn(fch, ref simple_flag);
                        bcn_cnt = ctrl_bcn.信标周期计数;
                        mpdu_len = (UInt32)(Payload.Length - 16);
                        desc.FCH = (Object)ctrl_bcn;
                    }

                    mpdu = new byte[mpdu_len];
                    if (HdrInfo.ProType == "GW")
                    {
                        Array.Copy(Payload1, 16, mpdu, 0, mpdu.Length);
                    }
                    else
                    {
                        Array.Copy(Payload, 16, mpdu, 0, mpdu.Length);
                    }

                    if (HdrInfo != null && HdrInfo.CRC == "ERROR")
                    {
                        desc.MPDU = (Object)mpdu;
                        break;
                    }

                    if (mpdu_len == 0)
                    {
                        break;
                    }

//#if NWSM
                    if (comFunc.BitField8(mpdu[0], 4, 1) == 1) //精简信标处理
                    {
                       beacon_pld_jj bcn_jj = Parse.bcn_payload_jj(mpdu, mpdu.Length, ref simple_flag, ref detail);
                       desc.MPDU = (Object)bcn_jj;
                       break;
                    }
//#endif
                    if (HdrInfo.ProType == "NW")
                    {
                        beacon_pld bcn = Parse.bcn_payload(mpdu, mpdu.Length, ref simple_flag, ref csma_union, ref detail);
                        desc.MPDU = (Object)bcn;
                    }
                    else
                    {
                        beacon_pld_gw bcn = Parse.bcn_payload_gw(mpdu, mpdu.Length, ref simple_flag, ref csma_union_gw, ref detail);
                        desc.MPDU = (Object)bcn;
                    }
                        
                    break;
                case 1:  //SOF帧
                    int pb_size = 0;
                    int pb_num = 0;
                    ctrl_sof ctl_sof;
                    ctrl_sof_gw ctl_sof_gw;
//#if NWSM
                    ctrl_sof_rf ctl_sof_rf;
                    ctrl_sof_rf_gw ctl_sof_rf_gw;

                    if (HdrInfo.ProType == "GW" && HdrInfo1.ChType == "无线")
                    {
                        ctl_sof_rf_gw = Parse.fch_sof_rf_gw(fch, ref simple_flag);
                        mpdu_len = ctl_sof_rf_gw.载荷PB大小;
                        pb_size = (int)mpdu_len;
                        pb_num = 1;
                        desc.FCH = (Object)ctl_sof_rf_gw;
                    }
                    else if (HdrInfo.ProType == "GW" && HdrInfo1.ChType == "载波")
                    {
                        ctl_sof_gw = Parse.fch_sof_gw(fch, ref simple_flag);
                        desc.FCH = (Object)ctl_sof_gw;
                        //int hop = 4 + 4; //跳过4个字节的头部 DD DD DD DD 和 4字节的物理块
                        pb_size = Parse.MPDU_check_gw(Payload1, Payload1.Length);
                        pb_num = ctl_sof_gw.物理块个数;

                    }

                    if (HdrInfo != null && HdrInfo.ProType == "NW" && HdrInfo.ChType == "HRF")
                    {
                        ctl_sof_rf = Parse.fch_sof_rf(fch, ref simple_flag);
                        mpdu_len = ctl_sof_rf.载荷PB大小;
                        pb_size = (int)mpdu_len;
                        pb_num = 1;
                        desc.FCH = (Object)ctl_sof_rf;
                    }
                    else if(HdrInfo.ProType == "NW")
//#endif
                    {
                        ctl_sof = Parse.fch_sof(fch, ref simple_flag);
                        desc.FCH = (Object)ctl_sof;
                        //int hop = 4 + 4; //跳过4个字节的头部 DD DD DD DD 和 4字节的物理块
                        pb_size = Parse.MPDU_check(Payload, Payload.Length);
                        pb_num = ctl_sof.物理块个数;
                    }

                    int expectedFullMacLength = (pb_size > 8 && pb_num > 0) ? (pb_size - 8) * pb_num : 0;
                    int availableFullMacLength = (HdrInfo.ProType == "GW" && Payload1 != null)
                        ? Math.Max(0, Payload1.Length - 16)
                        : 0;
                    if (HdrInfo.ProType == "GW" && expectedFullMacLength > availableFullMacLength)
                    {
                        byte[] partialMac = new byte[availableFullMacLength];
                        Array.Copy(Payload1, 16, partialMac, 0, partialMac.Length);
                        desc.MPDU = (Object)partialMac;
                        desc.Error = "国网物理块长度不足，已返回侦听台头、FCH和部分MPDU；"
                            + "期望MAC长度" + expectedFullMacLength.ToString()
                            + "，实际" + availableFullMacLength.ToString();
                        break;
                    }

                    if (pb_size > 0 &&( HdrInfo == null || HdrInfo.CRC != "ERROR"))
                    {
                        byte[] mac = new byte[(pb_size - 8) * pb_num];
                        for (int i = 0; i < pb_num; i++)
                        {
                            if (HdrInfo.ProType == "NW")
                            {
                                Array.Copy(Payload, 4 + 16 +  pb_size + 4 , mac, i * (pb_size - 8), pb_size - 8); //提取MAC帧
                            }
                            else
                            {
                                Array.Copy(Payload1, 16, mac, i * (pb_size - 8), pb_size - 8); //提取MAC帧
                            }
                                
                        }

                        if (Parse.MAC_check(mac, mac.Length) > 0)
                        {
                            sof_pld_c sof_pld = default(sof_pld_c);//default给其赋初值
 //#if NWSM
                            if (HdrInfo.ProType == "NW" && comFunc.BitField8(mac[0], 1, 2) == 2) //单跳帧协议
                            {
                                sof_pld = Parse.msdu_sof_single(mac, mac.Length, ref simple_flag, ref detail);
                                desc.MPDU = (Object)sof_pld;
                                break;
                            }

                            if (HdrInfo1.ProType == "GW" && comFunc.BitField8(mac[0], 0, 4) == 1) //国网单跳帧协议
                            {
                                sof_pld = Parse.msdu_sof_single_gw(mac, mac.Length, ref simple_flag, ref detail);
                                desc.MPDU = (Object)sof_pld;
                                break;
                            }
//#endif
                            if (HdrInfo.ProType == "NW" && comFunc.BitField8(mac[0], 0, 1) == 0) //长帧头
                            {
                                sof_pld = Parse.msdu_sof_l(mac, mac.Length, ref simple_flag, ref detail);
                            }
                            else if (HdrInfo.ProType == "NW")
                            {
                                sof_pld = Parse.msdu_sof_s(mac, mac.Length, ref simple_flag, ref detail);
                            }
                            if(HdrInfo1.ProType == "GW" && comFunc.BitField8(mac[0], 0, 4) == 0)
                            {
                                sof_pld = Parse.msdu_sof_gw(mac, mac.Length, ref simple_flag, ref detail);
                            }


                                desc.MPDU = (Object)sof_pld;
                        }
                        else
                        {
                            desc.MPDU = (Object)mac;
                        }

                    }
                    else
                    {
                        desc.MPDU = (Object)Payload;
                    }
                    break;
                case 2:
                    byte protype = 0;
                    if (HdrInfo1.ProType == "GW" && HdrInfo1.ChType == "无线")
                    {
                        ctrl_sack_rf_gw sack_rf_gw = Parse.fch_sack_rf_gw(fch, protype, ref detail);
                        desc.FCH = (Object)sack_rf_gw;
                        break;
                    }
                    else if (HdrInfo1.ProType == "GW" && HdrInfo1.ChType == "载波")
                    {
                        ctrl_sack_gw sack_gw = Parse.fch_sack_gw(fch, protype, ref detail);
                        desc.FCH = (Object)sack_gw;
                        break;
                    }


                    if (HdrInfo != null && HdrInfo.ProType == "NW" && HdrInfo.ProType == "HRF")
                    {
                        protype = 1;
                    }
                    ctrl_sack sack = Parse.fch_sack(fch, protype, ref detail);
                    desc.FCH = (Object)sack;
                    break;
                case 3:
                   if (HdrInfo.ProType == "GW"  && HdrInfo1.ChType == "载波")
                    {
                        ctrl_coord_gw coord_gw = Parse.fch_ccord_gw(fch);
                        desc.FCH = (Object)coord_gw;
                        break;
                    }
                    ctrl_coord coord = Parse.fch_ccord(fch);
                    desc.FCH = (Object)coord;
                    break;
                default:
                    
                    break;
            }

           jsonDesc = new JavaScriptSerializer().Serialize(desc);
        }

        /// <summary>
        /// DLL接口-全功能解析，用于详细展示帧内容
        /// </summary>
        /// <param name="packet"></param>
        /// <param name="length"></param>
        /// <param name="jsonDesc"></param>
        public void GetProtocolFch(byte[] packet, int length, out string jsonDesc)
        {
            //FCH获取
            int simple_flag = 0;
            byte[] fch = new byte[16];
            Array.Copy(packet, fch, fch.Length);
            int frmType = fch[0] & 0x07;
            Object Fch = null;
            //FCH解析
            switch (frmType)
            {
                case 0:   //信标帧
                    ctrl_beacon ctrl_bcn;
                    ctrl_bcn = Parse.fch_bcn(fch, ref simple_flag);
                    Fch = (Object)ctrl_bcn;
                    break;
                case 1:  //SOF帧
                    ctrl_sof ctl_sof;
                    ctl_sof = Parse.fch_sof(fch, ref simple_flag);
                    Fch = (Object)ctl_sof;
                    break;
                case 2:
                    string detail = "";
                    ctrl_sack sack = Parse.fch_sack(fch, 0, ref detail);
                    Fch = (Object)sack;
                    break;
                case 3:
                    ctrl_coord coord = Parse.fch_ccord(fch);
                    Fch = (Object)coord;
                    break;
                default:

                    break;
            }

            jsonDesc = new JavaScriptSerializer().Serialize(Fch);
        }


        

        public void GetProtocolMac(byte[] packet, int length, out string jsonDesc)
        {
            int simple_flag = 0;
            string detail = "";

            sof_pld_c sof_pld;
            if (comFunc.BitField8(packet[0], 0, 1) == 0) //长帧头
            {
                sof_pld = Parse.msdu_sof_l(packet, packet.Length, ref simple_flag, ref detail);
            }
            else
            {
                sof_pld = Parse.msdu_sof_s(packet, packet.Length, ref simple_flag, ref detail);
            }
            if (comFunc.BitField8(packet[0], 0, 4) == 0)
            {
                sof_pld = Parse.msdu_sof_gw(packet, packet.Length, ref simple_flag, ref detail);
            }


            jsonDesc = new JavaScriptSerializer().Serialize(sof_pld);
        }


        public void GetProtocolBeacon(byte[] packet, int length, out string jsonDesc)
        {
            int simple_flag = 0;
            string detail = "";
            csma_union_c csma_union = new csma_union_c();

            
            beacon_pld bcn = Parse.bcn_payload(packet, length, ref simple_flag, ref csma_union, ref detail);

            jsonDesc = new JavaScriptSerializer().Serialize(bcn);
        }

       

    }
}
