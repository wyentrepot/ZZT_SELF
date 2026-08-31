using snifferFrame;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
#if NET8_0_OR_GREATER
using System.Text.Json.Serialization;
#else
using System.Web.Script.Serialization;
#endif


/// <summary>
/// DLL接口描述定义
/// </summary>
namespace DllDesc
{
    /// <summary>
    /// 简单描述
    /// </summary>
    public class SimplDesc
    {
        public int PayLenth;        //载荷长度
        public FrmHdrInfo Info;     //帧头信息
        public string FrmType;      //帧类型
        public string SNID;            //短网络标识
        public string Msdu_seq;     //MSDU序列号
        public string SRC;          //源TEI
        public string DST;          //目的TEI
        public string ORI_S;        //原始源TEI
        public string FINL_D;       //原始目的TEI
        public string ADDR;         //源长地址
        public string TS_TYPE;      //时隙类型
        public UInt32 OFFSET;       //当前帧的接收时间距离信标周期开始时间的偏移量
        public UInt32 BCN_S;        //信标周期开始时间
        public UInt32 CSMA_S;       //信标周期开始时间
        public int frame_len;    //帧长度
        public string Detail;       //明细
        public string Debug;        //debug信息

        public string APP_PORT;     //APS应用层端口号（如 11），无有效APS时为null
        public string APP_ID;       //APS应用层报文ID（4位大写十六进制，如 00E4），无有效APS时为null
        public string APP_RAW;      //有界APS应用层字节（紧凑大写十六进制，自端口字节起），无有效APS时为null

        public FrmHdrInfo_gw Info2;     //国网帧头信息


        //public byte ver;      //版本号
        //public UInt32 crc24;  //校验 
    }

    /// <summary>
    /// 全功能描述
    /// </summary>
    public class FullDesc
    {
        public FrmHdrInfo_gw Info2;
        public string Error;
#if NET8_0_OR_GREATER
        [JsonIgnore] //保持 net48 ScriptIgnore 的输出契约
#else
        [ScriptIgnore] //忽略某一行的显示
#endif
        public FrmHdrInfo Info;
        public Object FCH;
        public Object MPDU;
       
    }




}
