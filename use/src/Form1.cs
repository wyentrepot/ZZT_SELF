using System;
using System.Windows.Forms;
using Newtonsoft.Json.Linq;
using _cUtils;
using NW;

namespace TestDllUse
{
    public partial class Form1 : Form
    {
        public Form1()
        {
            InitializeComponent();
        }

        private void Form1_Load(object sender, EventArgs e)
        {
            string name = null;
            string version = null;
            string date = null;

            NwHPLCAnalysis nwHPLC = new NwHPLCAnalysis();
            nwHPLC.GetProtocolVersion(out name, out version, out date);

            label1.Text = "DLL 信息:" + "name:" + name + " VER:" + version + " Date:" + date;
        }

        private void button1_Click(object sender, EventArgs e)
        {
            textBox1.Text = "";
            textBox2.Text = "";
            textBox3.Text = "";
            label3.Text = label3.Text = "";
        }

        private void button2_Click(object sender, EventArgs e)
        {
            myString myStr = new myString();
            if (textBox1.Text.Length == 0)
            {
                return;
            }

            byte[] pack =  myStr.strToHexByte(textBox1.Text);

            NwHPLCAnalysis nwHPLC = new NwHPLCAnalysis();
#if true
            nwHPLC.CheckProtocolInfo(pack, 0, pack.Length, out int index, out int length);
            label4.Text = "index:"+index.ToString() + "  length:" + length.ToString();

            byte[] data = new byte[length];
            Array.Copy(pack, index, data, 0, length);

            nwHPLC.GetProtocolSimpleDesc(data, data.Length, out string res);
            textBox2.Text = JToken.Parse(res).ToString(Newtonsoft.Json.Formatting.Indented);

            nwHPLC.GetProtocolFullDesc(data, data.Length, out string res2);
            textBox3.Text = JToken.Parse(res2).ToString(Newtonsoft.Json.Formatting.Indented);
#else

            byte[] data = new byte[pack.Length];
            Array.Copy(pack, 0, data, 0, pack.Length);

            nwHPLC.GetProtocolFch(data, data.Length, out string res2);
            textBox3.Text = JToken.Parse(res2).ToString(Newtonsoft.Json.Formatting.Indented);
#endif
        }
    }
}
