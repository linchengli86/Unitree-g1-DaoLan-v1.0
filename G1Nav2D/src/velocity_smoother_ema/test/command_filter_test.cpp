#include <velocity_smoother_ema/command_filter.hpp>

#include <cassert>
#include <cmath>
#include <limits>
#include <string>

using velocity_smoother_ema::CommandFilter;
using velocity_smoother_ema::FilterConfig;
using velocity_smoother_ema::PlanarCommand;

PlanarCommand command(double x = 0.6, double y = 0.0, double wz = 0.7)
{
    PlanarCommand result;
    result.x = x;
    result.y = y;
    result.wz = wz;
    return result;
}

void zero(const PlanarCommand& value)
{
    assert(value.x == 0.0 && value.y == 0.0 && value.wz == 0.0);
}

void close(double value, double wanted)
{
    assert(std::fabs(value - wanted) < 1e-10);
}

void five_hz()
{
    CommandFilter filter;
    PlanarCommand previous;
    for (int tick = 0; tick < 90; ++tick)
    {
        const double now = tick / 30.0;
        if (tick % 6 == 0)
            assert(filter.receive(command(), now));  // Planner publishes at 5 Hz.
        const PlanarCommand output = filter.update(now);
        assert(output.x >= previous.x && output.wz >= previous.wz);
        assert(output.x - previous.x <= 0.3 / 30.0 + 1e-10);
        assert(output.wz - previous.wz <= 0.5 / 30.0 + 1e-10);
        previous = output;
    }
    assert(previous.x > 0.599 && previous.x <= 0.6);
    assert(previous.wz > 0.699 && previous.wz <= 0.7);
}

void raw_separation()
{
    CommandFilter filter;
    assert(filter.receive(command(0.1, 0.1, 0.1), 0.0));
    PlanarCommand previous;
    for (int tick = 0; tick < 9; ++tick)
    {
        const PlanarCommand output = filter.update(tick / 30.0);
        assert(output.x > previous.x && output.y > previous.y && output.wz > previous.wz);
        previous = output;
    }
    assert(previous.x > 0.06);  // A recycled EMA output would plateau at its first step.
}

void acceleration()
{
    CommandFilter filter;
    assert(filter.receive(command(10.0, -10.0, 10.0), 1.0));
    const PlanarCommand first = filter.update(1.0);
    close(first.x, 0.01);
    close(first.y, -0.01);
    close(first.wz, 0.5 / 30.0);
    assert(filter.receive(command(-10.0, 10.0, -10.0), 1.005));
    const PlanarCommand second = filter.update(1.01);
    close(second.x, first.x - 0.3 * 0.01);
    close(second.y, first.y + 0.3 * 0.01);
    close(second.wz, first.wz - 0.5 * 0.01);
}

void immediate_stop()
{
    CommandFilter filter;
    assert(filter.receive(command(), 0.0));
    assert(filter.update(0.01).x > 0.0);
    assert(filter.receive(command(0.0, 0.0, 0.0), 0.011));
    zero(filter.update(0.012));
    zero(filter.update(0.03));
}

void timeout_boundary()
{
    FilterConfig config;
    CommandFilter filter(config);
    assert(filter.receive(command(), 0.0));
    assert(filter.update(config.raw_timeout - 1e-6).x > 0.0);
    zero(filter.update(config.raw_timeout));  // Exact 10/30 second boundary fails closed.
    zero(filter.update(0.34));
    assert(filter.receive(command(), 0.35));
    assert(filter.update(0.36).x > 0.0);
}

void invalid_command()
{
    const double invalid[] = {std::numeric_limits<double>::quiet_NaN(),
                              std::numeric_limits<double>::infinity(),
                              -std::numeric_limits<double>::infinity()};
    for (double value : invalid)
    {
        for (int axis = 0; axis < 3; ++axis)
        {
            CommandFilter filter;
            assert(filter.receive(command(), 0.0));
            assert(filter.update(0.01).x > 0.0);
            PlanarCommand malformed = command();
            if (axis == 0) malformed.x = value;
            if (axis == 1) malformed.y = value;
            if (axis == 2) malformed.wz = value;
            assert(!filter.receive(malformed, 0.02));
            zero(filter.update(0.03));
            zero(filter.update(0.04));
            assert(filter.receive(command(), 0.05));
            assert(filter.update(0.06).x > 0.0);
        }
    }
    CommandFilter filter;
    assert(filter.receive(command(), 0.0));
    assert(filter.update(0.01).x > 0.0);
    filter.reject(0.02);  // ROS adapter rejects invalid fields not used by planar output.
    zero(filter.update(0.03));
}

void bad_clock()
{
    CommandFilter filter;
    assert(filter.receive(command(), 1.0));
    assert(filter.update(1.01).x > 0.0);
    zero(filter.update(0.99));
    zero(filter.update(1.02));  // Old raw is not resurrected.
    assert(filter.receive(command(), 1.03));
    assert(filter.update(1.04).x > 0.0);
    assert(!filter.receive(command(), 1.02));
    zero(filter.update(1.03));
    assert(!filter.receive(command(), std::numeric_limits<double>::infinity()));
    zero(filter.update(1.04));
    assert(!filter.receive(command(), -1.0));
    zero(filter.update(1.05));
}

void callback_gap()
{
    CommandFilter filter;
    assert(filter.receive(command(), 0.0));
    assert(filter.update(0.01).x > 0.0);
    assert(filter.receive(command(), 0.39));
    zero(filter.update(0.4));  // Fresh raw cannot mask a timer stall exceeding watchdog.
    zero(filter.update(0.41));
    assert(filter.receive(command(), 0.42));
    assert(filter.update(0.43).x > 0.0);
    zero(filter.update(0.43));  // Non-positive duration cannot advance/hold old motion.
}

void configuration()
{
    for (int field = 0; field < 6; ++field)
    {
        FilterConfig config;
        if (field == 0) config.raw_timeout = 0.34;
        if (field == 1) config.acc_lim_x = 0.31;
        if (field == 2) config.acc_lim_theta = 0.51;
        if (field == 3) config.alpha_v = std::numeric_limits<double>::quiet_NaN();
        if (field == 4) config.alpha_w = 0.0;
        if (field == 5) config.period = 0.34;
        bool rejected = false;
        try { CommandFilter filter(config); }
        catch (const std::invalid_argument&) { rejected = true; }
        assert(rejected);
    }
}

int main(int argc, char** argv)
{
    assert(argc == 2);
    const std::string name(argv[1]);
    if (name == "five_hz") five_hz();
    else if (name == "raw_separation") raw_separation();
    else if (name == "acceleration") acceleration();
    else if (name == "immediate_stop") immediate_stop();
    else if (name == "timeout_boundary") timeout_boundary();
    else if (name == "invalid_command") invalid_command();
    else if (name == "bad_clock") bad_clock();
    else if (name == "callback_gap") callback_gap();
    else if (name == "configuration") configuration();
    else return 2;
    return 0;
}
