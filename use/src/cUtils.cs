using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace _cUtils
{
    /// <summary>
    ///  环形队列
    /// </summary>
    class myRingBuff
    {
        private int mask;       //掩码，队列大小-1，队列大小必须为2的整数幂
        private int indx_r;     //读索引
        private int indx_w;     //写索引 
        private byte[] buff;       //数据缓存   

        public myRingBuff(int size)
        {
            buff = new byte[size];
            mask = size - 1;
            indx_r = 0;
            indx_w = 0;
        }

        public int cnt()
        {
            return (indx_w - indx_r) & mask;
        }

        public void clear()
        {
            indx_r = 0;
            indx_w = 0;
        }

        public void put(byte[] dat, int len)
        {
            //fifo 放入数据
            int i;

            for (i = 0; i < len; i++)
            {
                buff[indx_w] = dat[i];
                indx_w++;
                indx_w &= mask;
                if (indx_w == indx_r)  //已满，则挤掉最前面的数据
                {
                    indx_r++;
                    indx_r &= mask;
                }
            }
        }

        //fifo  预获取数据
        //返回：实际数据长度
        public byte[] get()
        {
            int len = cnt();
            byte[] data = new byte[len];

            for (int i = 0; i < len; i++)
            {
                data[i] = buff[indx_r];
                indx_r++;
                indx_r &= mask;
            }

            return data;
        }
    }
    

class myString
    {
        private static bool isHexChar(char cc)
        {
            if ((cc == ' ') ||
                (cc >= '0' && cc <= '9') ||
                (cc >= 'A' && cc <= 'F') ||
                (cc >= 'a' && cc <= 'f'))
            {
                return true;
            }

            return false;
        }

        private static bool isHexString(string hexString)
        {
            int len = hexString.Length;

            char[] a = new char[hexString.Length + 20];

            hexString.CopyTo(0, a, 0, hexString.Length);

            for (int i = 0; i < len; i++)
            {
                if (isHexChar(a[i]) == false)
                {
                    return false;
                }
            }

            return true;
        }

        /// <summary>
        /// 字符串转16进制字节数组
        /// </summary>
        /// <param name="hexString"></param>
        /// <returns></returns>
        public byte[] strToHexByte(string hexString)
        {
            int len = hexString.Length;

            hexString = hexString.Replace(" ", "");
            hexString = hexString.Replace("\n", "");
            hexString = hexString.Replace("\r", "");
            hexString = hexString.Replace(".", "");
            hexString = hexString.Replace("-", "");
            hexString = hexString.Replace("_", "");

            if ((hexString.Length % 2) != 0)
            {
                hexString += "0";
            }

            byte[] returnBytes = new byte[hexString.Length / 2];

            for (int i = 0; i < returnBytes.Length; i++)
            {
                try
                {
                    returnBytes[i] = Convert.ToByte(hexString.Substring(i * 2, 2), 16);
                }
                catch
                {
                    returnBytes[i] = 0x00;
                };
            }

            return returnBytes;
        }
        public string hexToHexStr(byte[] buff)
        {
            string hexStr = "";

            for (int i = 0; i < buff.Length; i++)
            {
                hexStr += buff[i].ToString("X2") + " ";
            }

            return hexStr;
        }
        public string hexTocHexStr(byte[] buff)
        {
            string hexStr = "";

            for (int i = 0; i < buff.Length; i++)
            {
                hexStr += "0x" + buff[i].ToString("X2") + ",";
            }

            return hexStr;
        }
        public byte[] strToByte(string str)
        {
            int len = str.Length;
            byte[] returnBytes = System.Text.Encoding.Default.GetBytes(str);

            return returnBytes;
        }
        /// <summary>
        /// 不可见字符替换
        /// </summary>
        /// <param name="dat"></param>
        /// <returns></returns>
        public byte[] invisibleCharReplace(byte[] dat)
        {
            byte[] newDat = new byte[dat.Length];
            for (int i = 0; i < dat.Length; i++)
            {
                if (dat[i] > 0 && dat[i] < 128)
                {
                    newDat[i] = dat[i];
                }
                else
                {
                    newDat[i] = (byte)'*';
                }
            }

            return newDat;
        }
    }

    class myCheck
    {
        /// <summary>
        /// 和校验-8位
        /// </summary>
        /// <param name="dat"></param>
        /// <returns></returns>
        public byte checkSum_8(byte[] dat) 
        {
            byte sum = 0;
            for (int i = 0; i < dat.Length; i++)
            {
                sum += dat[i];
            }

            return sum;
        }

        public ushort checkSum_16(byte[] dat)
        {
            ushort sum = 0;
            for (int i = 0; i < dat.Length; i++)
            {
                sum += dat[i];
            }

            return sum;
        }

        public byte crc8(byte[] dat)
        {
            byte crc = 0;
            for (int i = 0; i < dat.Length; i++)
            {
                crc ^= dat[i];
                for (int j = 0; j < 8; j++)
                {
                    if ((crc & 0x80) > 0)
                    {
                        crc = (byte)((crc << 1) ^ 0x07);
                    }
                    else
                    {
                        crc <<= 1;
                    }
                }
            }
            return crc;
        }

        public UInt16 crc16_modbus(byte[] dat)
        {
            const UInt16 polynomial = 0x1021;
            const UInt16 initialValue = 0;
            UInt16 crc = initialValue;

            foreach (byte b in dat)// bytes)
            {
                crc ^= (ushort)(b << 8);
                for (int i = 0; i < 8; i++)
                {
                    if ((crc & 0x8000) != 0)
                    {
                        crc = (ushort)((crc << 1) ^ polynomial);
                    }
                    else
                    {
                        crc <<= 1;
                    }
                }
            }

            return crc; 
        }



        private static UInt32[] crc32_table = new UInt32[256];
        private const UInt32 CRC32_POLYNOMIAL = 0x04C11DB7;
        private const UInt32 UINT32_MSB_MASK = 0x80000000;
        private void init_i363_crc32_table()
        {
            UInt32 c = 0;
            int bit = 0;

            for (UInt32 i = 0; i < 256; i++)
            {
                c = i << 24;
                for (bit = 0; bit < 8; bit++)
                {
                    if ((c & UINT32_MSB_MASK) > 0)
                    {
                        c = (c << 1) ^ (CRC32_POLYNOMIAL);
                    }
                    else
                    {
                        c = c << 1;
                    }
                }
                crc32_table[i] = c;
            }
        }

        public UInt32 crc32_i363(byte[] dat)
        {
            UInt32 crc = 0xffffffff;
            init_i363_crc32_table();
            for (uint i = 0; i < dat.Length; i++) {
                crc = (crc << 8) ^ (crc32_table[((crc >> 24)^(UInt32)(dat[i])) & 0xff]);
            }
            return crc;
        }


        public Int32 crc32(byte[] dat)
        {
            Int32 CRC32_CODE = 0x04C11DB7;
            Int32 crc = 0x00000000;
            Int32 i, j;

            for (i = 0; i < dat.Length; i++)
            {
                crc ^= (((Int32)dat[i]) << 24);
                for (j = 8; j > 0; j--)
                {
                    if ((crc & 0x80000000) > 0)
                    {
                        crc <<= 1;
                        crc ^= CRC32_CODE;
                    }
                    else
                    {
                        crc <<= 1;
                    }
                }
            }
            return crc;
        }
    }
}