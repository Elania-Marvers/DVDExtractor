#ifndef DVDEXTRACTOR_ANALYSIS_ENTROPY_H_
#define DVDEXTRACTOR_ANALYSIS_ENTROPY_H_

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <type_traits>
#include <utility>
#include <vector>

#include "common/perf.h"

namespace dvdextractor::analysis {

namespace detail {
constexpr std::size_t kEntropyAlphabetSize = 256;
}

class ByteSumStrategy {
public:
    virtual ~ByteSumStrategy() = default;
    [[nodiscard]] virtual std::uint64_t compute(const std::uint8_t* data, std::size_t len) const = 0;
};

class AsmByteSum final : public ByteSumStrategy {
public:
    [[nodiscard]] std::uint64_t compute(const std::uint8_t* data, std::size_t len) const override;
};

class FallbackByteSum final : public ByteSumStrategy {
public:
    [[nodiscard]] std::uint64_t compute(const std::uint8_t* data, std::size_t len) const override;
};

// Politique de somme par défaut: asm si dispo, fallback sinon.
#if defined(DVD_DISABLE_ASM_SUM) || !DVD_HAS_ASM
using DefaultByteSumPolicy = FallbackByteSum;
#else
using DefaultByteSumPolicy = AsmByteSum;
#endif

// Conteneur de statistiques réutilisable avec stratégie de somme.
// Cette classe est pensée pour être inline/optimisée par le compilateur sur les
// parcours fréquents de buffers binaires.
template <typename Container, typename SumPolicy = DefaultByteSumPolicy>
class ByteStatistics {
public:
    explicit ByteStatistics(SumPolicy strategy = SumPolicy()) : strategy_(std::move(strategy)) {}

    [[nodiscard]] double entropy(const Container& data) const {
        using ValueType = typename Container::value_type;
        static_assert(std::is_integral_v<ValueType>, "Container value_type must be integral");
        static_assert(sizeof(ValueType) == 1, "Container value_type must be one-byte type");
        if (data.empty()) {
            return 0.0;
        }
        return entropy(data.data(), data.size());
    }

    [[nodiscard]] double entropy(const std::uint8_t* data, std::size_t len) const {
        if (data == nullptr || len == 0) {
            return 0.0;
        }

        std::array<std::size_t, detail::kEntropyAlphabetSize> histogram{};
        const auto* ptr = data;
        const auto* end = data + len;

        while (ptr < end) {
            ++histogram[static_cast<unsigned char>(*ptr)];
            ++ptr;
        }

        const double total = static_cast<double>(len);
        double result = 0.0;
        for (const auto count : histogram) {
            if (DVD_UNLIKELY(count == 0)) {
                continue;
            }
            const double probability = static_cast<double>(count) / total;
            result -= probability * std::log2(probability);
        }

        return result;
    }

    [[nodiscard]] std::uint64_t sum(const Container& data) const {
        using ValueType = typename Container::value_type;
        static_assert(std::is_integral_v<ValueType>, "Container value_type must be integral");
        static_assert(sizeof(ValueType) == 1, "Container value_type must be one-byte type");
        if (data.empty()) {
            return 0;
        }
        return sum(reinterpret_cast<const std::uint8_t*>(data.data()), data.size());
    }

    [[nodiscard]] std::uint64_t sum(const std::uint8_t* data, std::size_t len) const {
        if (data == nullptr || len == 0) {
            return 0;
        }
        return strategy_.compute(data, len);
    }

private:
    SumPolicy strategy_;
};

}  // namespace dvdextractor::analysis

#endif  // DVDEXTRACTOR_ANALYSIS_ENTROPY_H_
