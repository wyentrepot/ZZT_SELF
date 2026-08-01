using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;

namespace snifferFrame
{

    /// <summary>
    /// 侦听台头部信息帧格式
    /// </summary>
    public class FrmHdrInfo
    {
        public int InfoLen;       //帧头信息长度
        public string Data;       //帧头信息原始数据        
        public string CRC;        //CRC24校验状态
        public int RSSI;          //RSSI
        public string ProType;    //协议类型
        public int TMI;           //TMI
        public int TMI_EXT;       //TMI扩展 
        public int SN;            //帧序号
        public string ChType;     //信道类型 
        public UInt32 RxFchTime;  //接收FCH时间戳--侦听台时间
        public string RxFchTimes; //接收FCH时间戳--侦听台时间
        public int SFO;           //SFO-同步时钟误差
    }

    //国网侦听台
    /*public class FrmHdrInfo_gw
    {
        public byte _addr;         //标识报文由哪个侦听台设备上报此帧数据；这里固定为0xff
        public string _type;         //固定为 0x02，标识为营销双模
        public byte _id;           //保留域-定义位组
        public UInt16 _sn;         //上报序号
        public uint _ms;           //接收到报文同步字时，设备本地时间信息，ms字段
        public UInt16 _us;         //接收到报文同步字时，设备本地时间信息，us字段
        public UInt32 _ntb;        //报文产生时刻，网络时间信息；一个32位整数
        public string _port;         //报文接收通道， 0：载波； 1：无线；
        public byte _rssi;         //接收到信号时的信号强度
        public byte _snr;          //信噪比，取值范围：-127~127dB
        public int _len;        //payload 域的字节数
        public string _complete;     //标识完整性
        public string _error;        //payload域是否正确解调
        public string _payload;    //侦听到的报文内容；一个完整的mac层帧
        public string _crc;        //crc32，校验域从Address到Payload域
    }*/

    public class FrmHdrInfo_gw
    {
        public byte Type;
        public int InfoLen;       //帧头信息长度
        public int Data_len;      //信息域长度
        public string CRC;        //CRC24校验状态
        public int RSSI;          //RSSI
        public string ProType;    //协议类型
        public int TMI;           //TMI
        public int TMI_EXT;       //TMI扩展 
        public int SN;            //帧序号
        public string ChType;     //信道类型 public UInt32 RxFchTime;  //接收FCH时间戳--侦听台时间
        public string RxFchTimes; //接收FCH时间戳--侦听台时间
        public UInt32 RxFchTime;
        public int SFO;           //SFO-同步时钟误差
        public string Data;       //帧头信息原始数据        

    }

    public static class sniffer
    {
        private static byte[] PPP_Decode(byte[] dat)
        {
            int out_len = 0;
            byte[] out_buf = new byte[dat.Length * 2];
            byte tmp;

            for (int i = 0; i < dat.Length; i++)
            {
                tmp = dat[i];
                if (dat[i] == 0x7d)       //收到转义字符
                {
                    if (i >= (dat.Length - 1))
                    {
                        Console.WriteLine("ppp frm err!\n");
                        break;
                    }

                    if (dat[i + 1] == 0x5e)
                    {
                        out_buf[out_len++] = 0x7e;
                        i++;
                    }
                    else if (dat[i + 1] == 0x5d)
                    {
                        out_buf[out_len++] = 0x7d;
                        i++;
                    }
                    else
                    {
                       
                        //格式错误
                        Console.WriteLine("ppp frm err!\n");
                    }
                }
                else if (dat[i] == 0x7e ) 
                {
                    if (out_len > 0)
                    {
                        break;
                    }
                }
                else
                {
                    out_buf[out_len++] = dat[i];
                }
            }

            if (out_len == 0)
            {
                out_len = 1;
                out_buf[0] = 0;
            }

            byte[] dat_decode = new byte[out_len];
            Array.Copy(out_buf, dat_decode, out_len);
            return dat_decode;
        }

        private static byte[] PPP_Decode_gw(byte[] dat)
        {
            int out_len = 0;
            byte[] out_buf = new byte[dat.Length];

            // 国网侦听台封装帧以 0x7E 定界：跳过起始 0x7E，内容（含帧内的
            // 0x7E/0x7D 原始字节）原样保留，到末尾的 0x7E 结束。抓包导出的
            // 十六进制文本不再二次转义，因此 0x7D 不按 PPP 转义处理。
            int i = 0;
            if (dat.Length > 0 && dat[i] == 0x7e)
            {
                i++;
            }
            for (; i < dat.Length; i++)
            {
                if (dat[i] == 0x7e && i == dat.Length - 1)
                {
                    break; // 结尾定界符
                }
                out_buf[out_len++] = dat[i];
            }

            if (out_len == 0)
            {
                out_len = 1;
                out_buf[0] = 0;
            }

            byte[] dat_decode = new byte[out_len];
            Array.Copy(out_buf, dat_decode, out_len);
            return dat_decode;
        }

        private static FrmHdrInfo ParseFrmHead(byte[] dat, int lenth)
        {
            FrmHdrInfo info = new FrmHdrInfo();

            if (dat.Length < lenth)
            {
                return info;
            }
            if (lenth < 11)
            {
                return info;
            }
            info.InfoLen = lenth;
            info.Data = comFunc.ByteArryToHexStr(dat);
            info.CRC = (dat[0] == 0) ? "OK" : "ERROR";
            info.RSSI = dat[1];
            if (dat[2] == 0x0A)
            {
                info.ProType = "NW";
            }
            else if (dat[2] == 0x0B)
            {
                info.ProType = "GW";
            }
            else
            {
                info.ProType = "未知";
            }
            info.TMI = dat[3] & 0x0F;
            info.TMI_EXT = (dat[3] >> 4) & 0x0F;
            info.SN = dat[4];
            if (dat[5] == 0x00)
            {
                info.ChType = "HPLC";
            }
            else if (dat[5] == 0x02)
            {
                info.ChType = "HRF";
            }
            else
            {
                info.ChType = "未知";
            }
            info.RxFchTime = comFunc.ToUInt32(dat, 6);
            UInt32 time = info.RxFchTime / 25;
            UInt32 sec = time / 1000 / 1000;
            time -= sec * 1000 * 1000;
            UInt32 ms = time / 1000;
            time -= ms * 1000;
            info.RxFchTimes = sec.ToString() + ":" + ms.ToString("D3") + "." + time.ToString("D3");
            //info.RxFchTimes = info.RxFchTime.ToString();
            info.SFO = dat[10];
            return info;
        }

        /*private static FrmHdrInfo_gw ParseFrmHead_gw(byte[] dat, int lenth)
        {
            FrmHdrInfo_gw info = new FrmHdrInfo_gw();

            if (dat.Length < lenth)
            {
                return info;
            }
            if (lenth < 11)
            {
                return info;
            }
           

            info._addr = dat[0];
            info._type = comFunc.ByteArryToHexStr_4(dat,1,1);
            info._id = dat[2];
            info._sn = comFunc.ToUInt16(dat, 3);
            info._ms = comFunc.ToUInt32(dat, 5);
            info._us = comFunc.ToUInt16(dat, 9);
            info._ntb = comFunc.ToUInt32(dat, 11);
            info._port = (dat[15] == 0) ? "载波" : "无线";
            info._rssi = dat[16];
            info._snr = dat[17];
            info._len = comFunc.ToUInt16(dat, 18);
            info._complete = (dat[20] == 0) ? "不完整" : "完整";
            info._error = (dat[21] == 0) ? "正常" : "异常";
            info._payload = comFunc.ByteArryToHexStr_4(dat,22,info._len);
            info._crc =  comFunc.ByteArryToHexStr_3(dat, info._len + 22, 4);


            return info;
        }*/


        private static FrmHdrInfo_gw ParseFrmHead_gw(byte[] dat, int lenth)
        {
            FrmHdrInfo_gw info = new FrmHdrInfo_gw();

            if (dat.Length < lenth)
            {
                return info;
            }
            if (lenth < 11)
            {
                return info;
            }
            info.InfoLen = lenth;
            info.Data = comFunc.ByteArryToHexStr(dat);
            info.CRC = (dat[0] == 0) ? "OK" : "ERROR";
            info.RSSI = dat[1];
            if (dat[2] == 0x0A)
            {
                info.ProType = "NW";
            }
            else if (dat[2] == 0x0B)
            {
                info.ProType = "GW";
            }
            else
            {
                info.ProType = "未知";
            }
            info.TMI = dat[3] & 0x0F;
            info.TMI_EXT = (dat[3] >> 4) & 0x0F;
            info.SN = dat[4];
            if (dat[5] == 0x00)
            {
                info.ChType = "载波";
            }
            else if (dat[5] == 0x01)
            {
                info.ChType = "无线";
            }
            else
            {
                info.ChType = "未知";
            }
            info.RxFchTime = comFunc.ToUInt32(dat, 6);
            UInt32 time = info.RxFchTime / 25;
            UInt32 sec = time / 1000 / 1000;
            time -= sec * 1000 * 1000;
            UInt32 ms = time / 1000;
            time -= ms * 1000;
            info.RxFchTimes = sec.ToString() + ":" + ms.ToString("D3") + "." + time.ToString("D3");
            //info.RxFchTimes = info.RxFchTime.ToString();
            info.SFO = dat[10];
            return info;
        }

        private static FrmHdrInfo_gw ParseGwSnifferHead(byte[] dat, int payloadLength, int availableLength)
        {
            FrmHdrInfo_gw info = new FrmHdrInfo_gw();
            info.Type = dat[1];
            info.InfoLen = 22;
            info.Data_len = payloadLength;
            info.Data = comFunc.ByteArryToHexStr_4(dat, 0, 22);
            info.CRC = (dat[21] == 0) ? "OK" : "ERROR";
            info.RSSI = dat[16];
            info.ProType = "GW";
            info.SN = comFunc.ToUInt16(dat, 3);
            info.ChType = (dat[15] == 0) ? "载波" : "无线";
            info.RxFchTime = comFunc.ToUInt32(dat, 11);
            UInt32 time = info.RxFchTime / 25;
            UInt32 sec = time / 1000 / 1000;
            time -= sec * 1000 * 1000;
            UInt32 ms = time / 1000;
            time -= ms * 1000;
            info.RxFchTimes = sec.ToString() + ":" + ms.ToString("D3") + "." + time.ToString("D3");
            info.SFO = (payloadLength == availableLength) ? 0 : payloadLength - availableLength;
            return info;
        }


        /// <summary>
        /// 侦听台数据帧预处理
        /// </summary>
        /// <param name="packet"></param>
        /// <param name="length"></param>
        /// <param name="FrmHdrInfo"></param>
        /// <param name="pyload"></param>
        /// <returns></returns>
        public static int FrmPreprc(byte[] packet, int length, out FrmHdrInfo HdrInfo, out byte[] payload)
        {
            HdrInfo = null;
            payload = null;

            if (packet.Length < 10)
            {
                return -1;
            }

            byte[] frm = PPP_Decode(packet);//去掉7e的头和尾,7d也不要，5e和5d变7e和7d并跳过。最后得出剩余数据
            if (frm.Length < 10)
            {
                return -2;
            }


            /*if (frm[5] == 0x0b)
            {
                return 0;
            } */

            //头部解析
            int payLen = (((int)frm[0] << 8) + (int)frm[1]) >> 6;
            int infoLen = frm[2];
            //帧长度判断
            if ((3 + infoLen + payLen) > (frm.Length - 4))
            {
                return -3;
            }

            byte[] info = new byte[infoLen];
            Array.Copy(frm, 3, info, 0, info.Length);
            HdrInfo = ParseFrmHead(info, info.Length);

            payload = new byte[payLen];
            Array.Copy(frm, 3 + infoLen, payload, 0, payload.Length);//frm去掉最后4个数据和前infolen+3个数据

            return 0;
        }

        /// <summary>
        /// 国网侦听台数据帧预处理
        /// </summary>
        /// <param name="packet"></param>
        /// <param name="length"></param>
        /// <param name="FrmHdrInfo"></param>
        /// <param name="pyload"></param>
        /// <returns></returns>
        /*public static int FrmPreprc_gw(byte[] packet, int length, out FrmHdrInfo_gw HdrInfo1, out byte[] payload1)
        {
            HdrInfo1 = null;
            payload1 = null;

            if (packet.Length < 10)
            {
                return -1;
            }

            byte[] frm = PPP_Decode_gw(packet);
            if (frm.Length < 10)
            {
                return -2;
            }

            if (frm[0] != 0xFF && frm[1] != 0x02)
            {
                return 0;
            }          
            //头部解析
           
            int payLen = comFunc.ToUInt16(frm, 18);
            int infoLen = frm.Length;
            //帧长度判断
            if ( infoLen  > frm.Length )
            {
                return -3 ;
            }

            byte[] info = new byte[infoLen];
            Array.Copy(frm, 0, info, 0, info.Length);
            HdrInfo1 = ParseFrmHead_gw(info, info.Length);

             payload1 = new byte[payLen];
            Array.Copy(frm, 22, payload1, 0, payload1.Length);

            return 0;
        }*/

        public static int FrmPreprc_gw(byte[] packet, int length, out FrmHdrInfo_gw HdrInfo1, out byte[] payload1)
        {
            HdrInfo1 = null;
            payload1 = null;

            if (packet.Length < 10)
            {
                return -1;
            }

            byte[] frm = PPP_Decode_gw(packet);//去掉7e的头和尾,7d也不要，5e和5d变7e和7d并跳过。最后得出剩余数据
            if (frm.Length < 10)
            {
                return -2;
            }

            // 国网侦听台上报封装：FF 02 + 20字节头 + MAC帧 + CRC32。
            // 长度字段来自侦听设备，调试场景中可能与7E边界内的实际字节数不一致；
            // 始终以实际可用字节为上界，避免拒绝可继续分析的完整抓包。
            if (frm.Length >= 26 && frm[0] == 0xFF && frm[1] == 0x02)
            {
                int declaredPayloadLength = comFunc.ToUInt16(frm, 18);
                int availablePayloadLength = frm.Length - 22 - 4;
                int payloadLength = Math.Min(declaredPayloadLength, availablePayloadLength);
                if (payloadLength < 16)
                {
                    return -3;
                }

                HdrInfo1 = ParseGwSnifferHead(frm, declaredPayloadLength, availablePayloadLength);
                payload1 = new byte[payloadLength];
                Array.Copy(frm, 22, payload1, 0, payloadLength);
                return 0;
            }

            
            //头部解析
            int payLen = (((int)frm[0] << 8) + (int)frm[1]) >> 6;
            int infoLen = frm[2];
            //帧长度判断
            if ((3 + infoLen + payLen) > (frm.Length - 4))
            {
                return -3;
            }

            byte[] info = new byte[infoLen];
            Array.Copy(frm, 3, info, 0, info.Length);
            HdrInfo1 = ParseFrmHead_gw(info, info.Length);

            payload1 = new byte[payLen];
            Array.Copy(frm, 3 + infoLen, payload1, 0, payload1.Length);//frm去掉最后4个数据和前infolen+3个数据

            return 0;
        }
    }

}
