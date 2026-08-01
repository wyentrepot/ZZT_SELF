using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;

/// <summary>
/// 公共操作函数
/// </summary>
public static class comFunc
{
	/// <summary>
	/// 位段取值操作
	/// </summary>
	/// <param name="dat"></param>
	/// <param name="start">位段起始位置</param>
	/// <param name="size">位段大小</param>
	/// <returns>位段值</returns>
	public static UInt32 BitField32(UInt32 dat, int start, int size)
	{
		return (UInt32)((dat >> start) & ((1 << size) - 1));
	}
	public static UInt16 BitField16(UInt16 dat, int start, int size)
	{
		return (UInt16)((dat >> start) & ((1 << size) - 1));
	}
	public static byte BitField8(byte dat, int start, int size)
	{
		return (byte)((dat >> start) & ((1 << size) - 1));
	}

	public static UInt16 ToUInt16(byte[] dat, int start)
	{
		return (UInt16)(dat[start] + ((UInt16)dat[start + 1] << 8));
	}

	public static UInt32 ToUInt24(byte[] dat, int start)
	{
		return (UInt32)dat[start] + ((UInt32)dat[start + 1] << 8) + ((UInt32)dat[start + 2] << 16);
	}

	public static UInt32 ToUInt32(byte[] dat, int start)
	{
		return (UInt32)dat[start] + ((UInt32)dat[start + 1] << 8) + ((UInt32)dat[start + 2] << 16) + ((UInt32)dat[start + 3] << 24);
	}


	/// <summary>
	/// 字节数组转为HEX格式的字符串
	/// </summary>
	/// <param name="buff"></param>
	/// <returns></returns>
	public static string ByteArryToHexStr(byte[] buff)
	{
		string hexStr = "";

		for (int i = 0; i < buff.Length; i++)
		{
			hexStr += buff[i].ToString("X2") + " ";
		}

		return hexStr;
	}

    /// <summary>
    /// 字节数组转为HEX格式的字符串,不带空格
    /// </summary>
    /// <param name="buff"></param>
    /// <returns></returns>
    public static string ByteArryToHexStrWithoutBlock(byte[] buff)
    {
        string hexStr = "";

        for (int i = 0; i < buff.Length; i++)
        {
            hexStr += buff[i].ToString("X2");
        }

        return hexStr;
    }

    /// <summary>
    /// 字节数组转为HEX格式的字符串-格式2，无空格
    /// </summary>
    /// <param name="buff"></param>
    /// <returns></returns>
    public static string ByteArryToHexStr_2(byte[] buff)
	{
        if (buff == null)
        {
            return "null";
        }

        string hexStr = "";

		for (int i = 0; i < buff.Length; i++)
		{
			hexStr += buff[i].ToString("X2");
		}

		return hexStr;
	}

    /// <summary>
    /// 字节数组转为HEX格式的字符串-格式3，无空格且反转
    /// </summary>
    /// <param name="buff"></param>
    /// <returns></returns>
    public static string ByteArryToHexStr_3(byte[] buff, int satrt, int num)
    {
        string hexStr = "";

        for (int i = 0; i < num; i++)
        {
            hexStr += buff[satrt+num-1-i].ToString("X2");
        }

        return hexStr;
    }

    /// <summary>
    /// 字节数组转为HEX格式的字符串-格式3，无空格且不反转
    /// </summary>
    /// <param name="buff"></param>
    /// <returns></returns>
    public static string ByteArryToHexStr_4(byte[] buff, int satrt, int num)
    {
        string hexStr = "";

        for (int i = 0; i < num; i++)
        {
            hexStr += buff[satrt + i].ToString("X2");
        }

        return hexStr;
    }

}
