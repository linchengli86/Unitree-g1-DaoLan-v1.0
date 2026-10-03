#ifndef VELOCITY_SMOOTHER_EMA_COMMAND_FILTER_HPP
#define VELOCITY_SMOOTHER_EMA_COMMAND_FILTER_HPP

#include <algorithm>
#include <cmath>
#include <stdexcept>

namespace velocity_smoother_ema
{

struct PlanarCommand
{
    double x = 0.0;
    double y = 0.0;
    double wz = 0.0;
};

struct FilterConfig
{
    double alpha_v = 0.2;
    double alpha_w = 0.2;
    double period = 1.0 / 30.0;
    double raw_timeout = 1.0 / 3.0;
    double acc_lim_x = 0.3;
    double acc_lim_theta = 0.5;
};

// The caller supplies monotonic (steady-clock) seconds, never ROS/wall time.
// A timeout, invalid input or clock discontinuity drops both raw and output.
// After a fault, only a new valid command may restart the filter.
class CommandFilter
{
public:
    explicit CommandFilter(const FilterConfig& config = FilterConfig()) : config_(config)
    {
        if (!std::isfinite(config_.alpha_v) || config_.alpha_v <= 0.0 || config_.alpha_v > 1.0 ||
            !std::isfinite(config_.alpha_w) || config_.alpha_w <= 0.0 || config_.alpha_w > 1.0 ||
            !std::isfinite(config_.raw_timeout) || config_.raw_timeout <= 0.0 ||
            config_.raw_timeout > 1.0 / 3.0 ||
            !std::isfinite(config_.period) || config_.period <= 0.0 ||
            config_.period >= config_.raw_timeout ||
            !std::isfinite(config_.acc_lim_x) || config_.acc_lim_x <= 0.0 || config_.acc_lim_x > 0.3 ||
            !std::isfinite(config_.acc_lim_theta) || config_.acc_lim_theta <= 0.0 ||
            config_.acc_lim_theta > 0.5)
        {
            throw std::invalid_argument("Invalid smoother configuration; timeout/acceleration cannot exceed safety profile");
        }
    }

    bool receive(const PlanarCommand& command, double now)
    {
        if (!observe_clock(now))
            return false;
        if (!std::isfinite(command.x) || !std::isfinite(command.y) || !std::isfinite(command.wz))
        {
            clear_command();
            return false;
        }
        raw_ = command;
        raw_time_ = now;
        have_raw_ = true;
        if (raw_.x == 0.0 && raw_.y == 0.0 && raw_.wz == 0.0)
            output_ = PlanarCommand();  // A stop is never delayed by EMA/deceleration.
        return true;
    }

    void reject(double now)
    {
        observe_clock(now);
        clear_command();
    }

    PlanarCommand update(double now)
    {
        if (!observe_clock(now))
            return output_;
        const double dt = have_tick_ ? now - tick_time_ : config_.period;
        tick_time_ = now;
        have_tick_ = true;
        if (!std::isfinite(dt) || dt <= 0.0 || dt > config_.raw_timeout || !have_raw_ ||
            now - raw_time_ < 0.0 || now - raw_time_ >= config_.raw_timeout)
        {
            clear_command();
            return output_;
        }
        // Always approach the original input, not the last published output.
        output_.x = approach(output_.x, raw_.x, config_.alpha_v, config_.acc_lim_x * dt);
        output_.y = approach(output_.y, raw_.y, config_.alpha_v, config_.acc_lim_x * dt);
        output_.wz = approach(output_.wz, raw_.wz, config_.alpha_w, config_.acc_lim_theta * dt);
        return output_;
    }

private:
    static double approach(double previous, double raw, double alpha, double max_delta)
    {
        // Convex form avoids overflow when finite inputs have opposite signs.
        const double wanted = alpha * raw + (1.0 - alpha) * previous;
        const double step = std::max(-max_delta, std::min(max_delta, wanted - previous));
        return previous + step;
    }

    void clear_command()
    {
        raw_ = PlanarCommand();
        output_ = PlanarCommand();
        have_raw_ = false;
    }

    bool observe_clock(double now)
    {
        if (!std::isfinite(now) || now < 0.0 || (have_clock_ && now < clock_time_))
        {
            clear_command();
            have_tick_ = false;
            have_clock_ = std::isfinite(now) && now >= 0.0;
            if (have_clock_)
                clock_time_ = now;
            return false;
        }
        clock_time_ = now;
        have_clock_ = true;
        return true;
    }

    FilterConfig config_;
    PlanarCommand raw_;
    PlanarCommand output_;
    double raw_time_ = 0.0;
    double tick_time_ = 0.0;
    double clock_time_ = 0.0;
    bool have_raw_ = false;
    bool have_tick_ = false;
    bool have_clock_ = false;
};

}  // namespace velocity_smoother_ema

#endif
