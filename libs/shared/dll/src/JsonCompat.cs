using System;

#if NET8_0_OR_GREATER
using System.Collections.Generic;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;
#else
using System.Web.Script.Serialization;
#endif

namespace NW
{
    /// <summary>
    /// Keeps the DLL JSON boundary stable for .NET Framework and .NET 8.
    /// </summary>
    internal static class JsonCompat
    {
#if NET8_0_OR_GREATER
        private static readonly JsonSerializerOptions Options = new JsonSerializerOptions
        {
            IncludeFields = true,
            DefaultIgnoreCondition = JsonIgnoreCondition.Never,
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            WriteIndented = false,
            Converters = { new ByteArrayAsNumberArrayConverter() },
        };

        /// <summary>
        /// JavaScriptSerializer exposes byte[] as a JSON number array. System.Text.Json
        /// defaults to Base64, so keep the public parser response contract explicit.
        /// </summary>
        private sealed class ByteArrayAsNumberArrayConverter : JsonConverter<byte[]>
        {
            public override byte[] Read(
                ref Utf8JsonReader reader,
                Type typeToConvert,
                JsonSerializerOptions options)
            {
                if (reader.TokenType != JsonTokenType.StartArray)
                    throw new JsonException("Expected a byte array JSON value.");

                var values = new List<byte>();
                while (reader.Read() && reader.TokenType != JsonTokenType.EndArray)
                {
                    values.Add(reader.GetByte());
                }
                return values.ToArray();
            }

            public override void Write(
                Utf8JsonWriter writer,
                byte[] value,
                JsonSerializerOptions options)
            {
                writer.WriteStartArray();
                foreach (byte item in value)
                {
                    writer.WriteNumberValue(item);
                }
                writer.WriteEndArray();
            }
        }
#endif

        public static string Serialize(object value)
        {
#if NET8_0_OR_GREATER
            return JsonSerializer.Serialize(value, Options);
#else
            return new JavaScriptSerializer().Serialize(value);
#endif
        }
    }
}
