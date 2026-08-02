<!-- converted from 侦听台上位机软件协议解析动态库接口说明.docx -->


侦听台上位机软件协议解析动态库接口说明

侦听台协议解析动态库为侦听台上位机软件提供协议校验、解析功能，上位机软件通过加载解析动态库，可以动态的对通信链路上接收到的报文信息进行校验，获取有效的报文内容，并对报文内容进行解析，或者报文的解析说明内容。

动态库接口说明：

1、获取解析库描述信息

2、校验报文完整性
3、获取报文简单描述

4、获取报文完整描述

| 接口名称 | GetProtocolVersion(out string name, out string version, out string date) | GetProtocolVersion(out string name, out string version, out string date) | GetProtocolVersion(out string name, out string version, out string date) |
| --- | --- | --- | --- |
| 接口描述 | 获取解析库描述信息，包含接口库名称、版本和发布时间 | 获取解析库描述信息，包含接口库名称、版本和发布时间 | 获取解析库描述信息，包含接口库名称、版本和发布时间 |
| 请求信息 | 请求信息 | 请求信息 | 请求信息 |
| 参数名称 | 参数类型 | 参数描述 | 备注 |
| 无 |  |  |  |
| 返回信息 | 返回信息 | 返回信息 | 返回信息 |
| 参数名称 | 参数类型 | 参数描述 | 备注 |
| name | 字符串 | 协议名称 | 如:营销双模 |
| version | 字符串 | 协议版本 | 如:1.0.0.0 |
| date | 字符串 | 发布日期 | 如:2023-12-16 |
| 接口名称 | CheckProtocolInfo(byte[] revPacketBuffer, int startIndex, int bufferLen, out int index, out int length) | CheckProtocolInfo(byte[] revPacketBuffer, int startIndex, int bufferLen, out int index, out int length) | CheckProtocolInfo(byte[] revPacketBuffer, int startIndex, int bufferLen, out int index, out int length) |
| --- | --- | --- | --- |
| 接口描述 | 校验接收缓冲区是否包含有效报文信息，当接收缓冲区的数据无效时需要设置index标签位置为当前缓冲区最大值 | 校验接收缓冲区是否包含有效报文信息，当接收缓冲区的数据无效时需要设置index标签位置为当前缓冲区最大值 | 校验接收缓冲区是否包含有效报文信息，当接收缓冲区的数据无效时需要设置index标签位置为当前缓冲区最大值 |
| 请求信息 | 请求信息 | 请求信息 | 请求信息 |
| 参数名称 | 参数类型 | 参数描述 | 备注 |
| revPacketBuffer | 字节数组 | 接收缓冲区 |  |
| startIndex | 整型数字 | 接收缓冲区起始读取位置 |  |
| bufferLen | 整型数字 | 接收缓冲区长度 |  |
| 返回信息 | 返回信息 | 返回信息 | 返回信息 |
| 参数名称 | 参数类型 | 参数描述 | 备注 |
| index | 整型数字 | 接收缓冲区已读取位置 | 当值大于等于接收缓冲区长度时，缓冲区的所有数据将被抛弃 |
| length | 整型数字 | 有效报文长度 | 当值大于零时，将会以index作为起始位置获取指定长度缓冲区数据作为有效报文 |
| 接口名称 | GetProtocolSimpleDesc(byte[] packet, int length, out string desc) | GetProtocolSimpleDesc(byte[] packet, int length, out string desc) | GetProtocolSimpleDesc(byte[] packet, int length, out string desc) |
| --- | --- | --- | --- |
| 接口描述 | 获取报文简要描述信息 | 获取报文简要描述信息 | 获取报文简要描述信息 |
| 请求信息 | 请求信息 | 请求信息 | 请求信息 |
| 参数名称 | 参数类型 | 参数描述 | 备注 |
| packet | 字节数组 | 报文内容 |  |
| length | 整形数字 | 报文长度 |  |
| 返回信息 | 返回信息 | 返回信息 | 返回信息 |
| 参数名称 | 参数类型 | 参数描述 | 备注 |
| desc | 字符串 | 报文内容简要描述 | 用于监听列表中快速对监听接收报文内容进行阅览 |
| 接口名称 | GetProtocolFullDesc(byte[] packet, int length, out string jsonDesc) | GetProtocolFullDesc(byte[] packet, int length, out string jsonDesc) | GetProtocolFullDesc(byte[] packet, int length, out string jsonDesc) |
| --- | --- | --- | --- |
| 接口描述 | 获取报文完整描述信息，信息包含了完整的报文分层解析内容展示，采用json结构进行输出 | 获取报文完整描述信息，信息包含了完整的报文分层解析内容展示，采用json结构进行输出 | 获取报文完整描述信息，信息包含了完整的报文分层解析内容展示，采用json结构进行输出 |
| 请求信息 | 请求信息 | 请求信息 | 请求信息 |
| 参数名称 | 参数类型 | 参数描述 | 备注 |
| packet | 字节数组 | 报文内容 |  |
| length | 整形数字 | 报文长度 |  |
| 返回信息 | 返回信息 | 返回信息 | 返回信息 |
| 参数名称 | 参数类型 | 参数描述 | 备注 |
| jsonDesc | 字符串 | 报文内容完整描述 | 采用json字符串格式 |